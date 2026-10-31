"""
骰宝游戏服务
支持多种下注类型和定时开庄系统
"""

import asyncio
import random
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

from ..utils import get_now
from ..repositories.abstract_repository import AbstractUserRepository, AbstractLogRepository
import logging
logger = logging.getLogger(__name__)
@dataclass
class SicboBet:
    """骰宝下注记录"""
    user_id: str
    bet_type: str  # 下注类型：大、小、豹子、一点、二点等
    amount: int  # 下注金额
    odds: float  # 赔率
    created_at: datetime = field(default_factory=get_now)


@dataclass
class SicboGame:
    """骰宝游戏房间"""
    game_id: str
    start_time: datetime
    end_time: datetime
    bets: List[SicboBet] = field(default_factory=list)
    total_pot: int = 0  # 总奖池
    is_active: bool = True
    is_settled: bool = False
    dice_result: Optional[List[int]] = None
    # 保存游戏相关的上下文信息
    platform: Optional[str] = None  # 平台信息
    session_id: Optional[str] = None  # 会话ID
    session_info: Optional[Dict[str, Any]] = None  # 完整会话信息用于主动发送
    chat_id: Optional[str] = None  # 群聊ID


class SicboService:
    """骰宝游戏服务"""
    
    def __init__(self, user_repo: AbstractUserRepository, log_repo: AbstractLogRepository, config: Dict[str, Any]):
        self.user_repo = user_repo
        self.log_repo = log_repo
        self.config = config
        
        # 游戏配置
        sicbo_config = config.get("sicbo", {})
        self.countdown_seconds = sicbo_config.get("countdown_seconds", 60)  # 倒计时秒数，默认60秒
        self.min_bet = sicbo_config.get("min_bet", 100)  # 最小下注
        self.max_bet = sicbo_config.get("max_bet", 1000000)  # 最大下注
        self.message_mode = sicbo_config.get("message_mode", "image")  # 消息模式：image(图片) 或 text(文本)
        
        # 多会话游戏支持
        self.games: Dict[str, SicboGame] = {}  # session_id -> SicboGame
        self.countdown_tasks: Dict[str, asyncio.Task] = {}  # session_id -> countdown_task
        
        # 消息发送回调函数
        self.message_callback = None
        
        # 赔率表 - 基于您提供的图片
        self.odds_table = {
            # 大小和单双
            "大": 1.0,      # 1:1
            "小": 1.0,      # 1:1
            "单": 1.0,      # 1:1  (奇数)
            "双": 1.0,      # 1:1  (偶数)
            
            # 豹子 (三个骰子相同)
            "豹子": 24.0,    # 1:24
            
            # 指定点数 (押中特定点数出现)
            "一点": 0.0,    # 押注时根据出现次数确定赔率
            "二点": 0.0,
            "三点": 0.0,
            "四点": 0.0,
            "五点": 0.0,
            "六点": 0.0,
            
            # 总点数
            "4点": 50.0,    # 1:50
            "5点": 18.0,    # 1:18
            "6点": 14.0,    # 1:14
            "7点": 12.0,    # 1:12
            "8点": 8.0,     # 1:8
            "9点": 6.0,     # 1:6
            "10点": 6.0,    # 1:6
            "11点": 6.0,    # 1:6
            "12点": 6.0,    # 1:6
            "13点": 8.0,    # 1:8
            "14点": 12.0,   # 1:12
            "15点": 14.0,   # 1:14
            "16点": 18.0,   # 1:18
            "17点": 50.0    # 1:50
        }
    
    def set_message_callback(self, callback):
        """设置消息发送回调函数"""
        self.message_callback = callback
    
    def set_countdown_seconds(self, seconds: int) -> Dict[str, Any]:
        """设置倒计时秒数"""
        if seconds < 10:
            return {"success": False, "message": "❌ 倒计时不能少于10秒"}
        if seconds > 300:
            return {"success": False, "message": "❌ 倒计时不能超过300秒（5分钟）"}
        
        self.countdown_seconds = seconds
        return {
            "success": True, 
            "message": f"✅ 骰宝倒计时已设置为 {seconds} 秒"
        }
    
    def get_countdown_seconds(self) -> int:
        """获取当前倒计时秒数"""
        return self.countdown_seconds
    
    def set_message_mode(self, mode: str) -> Dict[str, Any]:
        """设置消息模式"""
        if mode not in ["image", "text"]:
            return {"success": False, "message": "❌ 无效的消息模式，请使用 'image' 或 'text'"}
        
        self.message_mode = mode
        mode_name = "图片模式" if mode == "image" else "文本模式"
        return {
            "success": True, 
            "message": f"✅ 骰宝消息模式已设置为 {mode_name}"
        }
    
    def get_message_mode(self) -> str:
        """获取当前消息模式"""
        return self.message_mode
    
    def is_image_mode(self) -> bool:
        """判断是否为图片模式"""
        return self.message_mode == "image"
    
    def start_new_game(self, session_id: str, session_info: Dict[str, Any] = None) -> Dict[str, Any]:
        """开启新的骰宝游戏"""
        # 检查当前会话是否已有游戏
        current_game = self.games.get(session_id)
        if current_game and current_game.is_active:
            remaining_time = (current_game.end_time - get_now()).total_seconds()
            if remaining_time > 0:
                return {
                    "success": False,
                    "message": f"❌ 当前会话已有游戏进行中，剩余时间 {int(remaining_time)} 秒"
                }
            # 到期不等于已结算；命令可能先于后台结算取得锁，不能覆盖已扣款的下注。
            return {"success": False, "message": "❌ 当前游戏正在开奖中，请等待结算完成后再开庄"}
        
        # 创建新游戏
        now = get_now()
        game_id = f"sicbo_{session_id}_{now.strftime('%Y%m%d_%H%M%S')}"
        
        new_game = SicboGame(
            game_id=game_id,
            start_time=now,
            end_time=now + timedelta(seconds=self.countdown_seconds),
            bets=[],
            total_pot=0,
            is_active=True,
            is_settled=False,
            session_id=session_id,
            session_info=session_info
        )
        
        # 保存游戏到会话字典
        self.games[session_id] = new_game
        
        # 取消旧的倒计时任务（如果存在）
        old_task = self.countdown_tasks.get(session_id)
        if old_task:
            old_task.cancel()
        
        # 启动新的倒计时任务
        # Native lifecycle scheduler owns deadlines and durable recovery. Do not inherit
        # an open command transaction into a detached asyncio task.
        
        logger.info(f"开启骰宝游戏: {game_id}, 会话: {session_id}, 倒计时 {self.countdown_seconds} 秒")
        
        return {
            "success": True,
            "message": f"🎲 骰宝游戏开庄！倒计时 {self.countdown_seconds} 秒\n\n"
                      f"📋 下注说明：\n"
                      f"• 鸭大/小：/鸭大 金额 或 /鸭小 金额\n"
                      f"• 鸭单/双：/鸭单 金额 或 /鸭双 金额\n"
                      f"• 鸭豹子：/鸭豹子 金额\n"
                      f"• 鸭点数：/鸭一点 金额 (一点~六点)\n"
                      f"• 鸭总点：/鸭4点 金额 (4点~17点)\n\n"
                      f"💰 下注范围：{self.min_bet:,} - {self.max_bet:,} 金币\n"
                      f"⏰ 倒计时结束后自动开奖！",
            "game_id": game_id,
            "end_time": new_game.end_time
        }
    
    def place_bet(self, user_id: str, bet_type: str, amount: int, session_id: str) -> Dict[str, Any]:
        """下注"""
        # 获取当前会话的游戏
        current_game = self.games.get(session_id)
        if not current_game or not current_game.is_active:
            return {"success": False, "message": "❌ 当前会话没有进行中的游戏，请发送 '/开庄' 开启新游戏"}
        
        # 检查游戏是否还在下注时间内
        remaining_time = (current_game.end_time - get_now()).total_seconds()
        if remaining_time <= 0:
            return {"success": False, "message": "❌ 下注时间已结束"}
        
        # 验证用户
        user = self.user_repo.get_by_id(user_id)
        if not user:
            return {"success": False, "message": "❌ 用户不存在，请先注册"}
        
        # 验证下注金额
        if amount < self.min_bet:
            return {"success": False, "message": f"❌ 最小下注金额为 {self.min_bet:,} 金币"}
            # 移除单笔最大下注限制：不再对单笔下注设置上限（如需限额请在配置或外部钱包策略中处理）
        
        if not user.can_afford(amount):
            return {"success": False, "message": f"❌ 金币不足！当前拥有 {user.coins:,} 金币"}
        
        # 验证下注类型
        normalized_bet_type = self._normalize_bet_type(bet_type)
        if not normalized_bet_type:
            return {"success": False, "message": f"❌ 无效的下注类型：{bet_type}"}
        
        odds = self._get_odds(normalized_bet_type)
        
        # 检查是否已有相同类型的下注，如果有则合并
        existing_bet = None
        for bet in current_game.bets:
            if bet.user_id == user_id and bet.bet_type == normalized_bet_type:
                existing_bet = bet
                break
        
        # 扣除金币
        user.coins -= amount
        self.user_repo.update(user)
        
        if existing_bet:
            # 合并下注：更新金额，保持最新的下注时间
            original_amount = existing_bet.amount
            existing_bet.amount += amount
            existing_bet.created_at = get_now()
            current_game.total_pot += amount
            
            # 计算用户在本局的总下注
            user_total_bet = sum(b.amount for b in current_game.bets if b.user_id == user_id)
            
            return {
                "success": True,
                "message": f"✅ 下注成功！(已合并)\n"
                          f"🎯 下注类型：{normalized_bet_type}\n"
                          f"💰 本次下注：{amount:,} 金币\n"
                          f"📈 原有下注：{original_amount:,} 金币\n"
                          f"🏆 合并后总额：{existing_bet.amount:,} 金币\n"
                          f"📊 赔率：1:{odds}\n"
                          f"💳 您本局总下注：{user_total_bet:,} 金币\n"
                          f"⏰ 剩余时间：{int(remaining_time)} 秒",
                "remaining_time": int(remaining_time),
                "merged": True,
                "original_amount": original_amount,
                "new_total": existing_bet.amount
            }
        else:
            # 添加新的下注记录
            bet = SicboBet(
                user_id=user_id,
                bet_type=normalized_bet_type,
                amount=amount,
                odds=odds
            )
            
            current_game.bets.append(bet)
            current_game.total_pot += amount
            
            # 计算用户在本局的总下注
            user_total_bet = sum(b.amount for b in current_game.bets if b.user_id == user_id)
            
            return {
                "success": True,
                "message": (
                    f"✅ 下注成功！\n"
                    f"💰 下注下限：{self.min_bet:,} 金币（单笔无上限）\n"
                    f"🎯 下注类型：{normalized_bet_type}\n"
                    f"💰 下注金额：{amount:,} 金币\n"
                    f"📊 赔率：1:{odds}\n"
                    f"💳 您本局总下注：{user_total_bet:,} 金币\n"
                    f"⏰ 剩余时间：{int(remaining_time)} 秒"
                ),
                "remaining_time": int(remaining_time),
                "merged": False
            }
    
    def get_game_status(self, session_id: str) -> Dict[str, Any]:
        """获取当前游戏状态"""
        game = self.games.get(session_id)
        if not game:
            return {
                "success": False,
                "message": "🎲 当前会话没有进行中的游戏\n发送 '/开庄' 开启新游戏",
                "has_game": False
            }
        
        if not game.is_active:
            return {
                "success": False,
                "message": "🎲 当前会话没有进行中的游戏\n发送 '/开庄' 开启新游戏",
                "has_game": False
            }
        
        remaining_time = (game.end_time - get_now()).total_seconds()
        if remaining_time <= 0:
            return {
                "success": False,
                "message": "⏰ 下注时间已结束，正在开奖中...",
                "has_game": True,
                "is_betting": False
            }
        
        # 统计下注信息
        bet_stats = {}
        for bet in game.bets:
            if bet.bet_type not in bet_stats:
                bet_stats[bet.bet_type] = {"count": 0, "amount": 0}
            bet_stats[bet.bet_type]["count"] += 1
            bet_stats[bet.bet_type]["amount"] += bet.amount
        
        total_bets = len(game.bets)
        unique_players = len(set(bet.user_id for bet in game.bets))
        
        # 返回结构化数据供图片生成使用
        return {
            "success": True,
            "has_game": True,
            "is_betting": True,
            "game_data": {
                "remaining_time": int(remaining_time),
                "total_bets": total_bets,
                "total_amount": game.total_pot,
                "unique_players": unique_players,
                "bets": bet_stats
            }
        }
    
    async def _countdown_task(self, session_id: str):
        """倒计时任务"""
        try:
            await asyncio.sleep(self.countdown_seconds)
            
            # 获取对应会话的游戏
            game = self.games.get(session_id)
            if game and game.is_active:
                # 结算游戏
                result = await self._settle_game(session_id)
                
                # 使用游戏中保存的会话信息发送结果公告
                if self.message_callback and result.get("success") and game.session_info:
                    try:
                        await self.message_callback(game.session_info, result)
                    except Exception as e:
                        logger.error(f"发送骰宝结果公告失败: {e}")
                
        except asyncio.CancelledError:
            logger.info(f"骰宝倒计时任务被取消 (会话: {session_id})")
        except Exception as e:
            logger.error(f"骰宝倒计时任务错误 (会话: {session_id}): {e}")
        finally:
            # 清理任务引用
            if session_id in self.countdown_tasks:
                del self.countdown_tasks[session_id]
    
    async def force_settle_game(self, session_id: str) -> Dict[str, Any]:
        """管理员强制结算游戏（跳过倒计时）"""
        game = self.games.get(session_id)
        if not game or not game.is_active:
            return {"success": False, "message": "❌ 当前会话没有进行中的游戏"}
        
        # 取消倒计时任务
        task = self.countdown_tasks.get(session_id)
        if task:
            task.cancel()
            del self.countdown_tasks[session_id]
        
        # 直接结算游戏
        result = await self._settle_game(session_id)
        
        # 使用游戏中保存的会话信息发送结果公告
        if self.message_callback and result.get("success") and game.session_info:
            try:
                await self.message_callback(game.session_info, result)
            except Exception as e:
                logger.error(f"发送骰宝结果公告失败: {e}")
        
        return result
    
    async def _settle_game(self, session_id: str) -> Dict[str, Any]:
        """结算游戏"""
        game = self.games.get(session_id)
        if not game or game.is_settled:
            return {"success": False, "message": "游戏已结算或不存在"}
        
        game.is_active = False
        
        # 投掷三个骰子
        dice = [random.randint(1, 6) for _ in range(3)]
        game.dice_result = dice
        total = sum(dice)
        
        # 判断各种结果
        results = self._analyze_dice_result(dice, total)
        
        # 结算所有下注
        settlement_info = []
        total_payout = 0
        
        for bet in game.bets:
            win = self._check_bet_win(bet, results)
            payout = 0
            
            if win:
                if bet.bet_type in ["一点", "二点", "三点", "四点", "五点", "六点"]:
                    # 点数下注根据出现次数计算赔率
                    point = int(bet.bet_type[0]) if bet.bet_type[0].isdigit() else {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}[bet.bet_type[0]]
                    count = dice.count(point)
                    if count == 1:
                        payout = bet.amount * 2  # 1:1
                    elif count == 2:
                        payout = bet.amount * 3  # 1:2
                    elif count == 3:
                        payout = bet.amount * 4  # 1:3
                    else:
                        payout = bet.amount  # 返还本金
                else:
                    payout = bet.amount * (1 + bet.odds)  # 本金 + 奖金
                
                total_payout += payout
                
                # 给用户加钱
                user = self.user_repo.get_by_id(bet.user_id)
                if user:
                    user.coins += payout
                    self.user_repo.update(user)
            
            settlement_info.append({
                "user_id": bet.user_id,
                "bet_type": bet.bet_type,
                "amount": bet.amount,
                "win": win,
                "payout": payout,
                "profit": payout - bet.amount if win else -bet.amount
            })
        
        game.is_settled = True
        
        # 生成结算消息
        dice_emojis = {1: '⚀', 2: '⚁', 3: '⚂', 4: '⚃', 5: '⚄', 6: '⚅'}
        dice_str = " ".join([dice_emojis.get(d, str(d)) for d in dice])
        
        message = f"🎲 骰宝开奖结果\n"
        message += f"🎯 骰子结果：{dice_str}\n"
        message += f"📊 总点数：{total} 点\n"
        message += f"🔍 判定：{'大' if total >= 11 else '小'}"
        
        if results["is_triple"]:
            message += f" (豹子！)"
        
        if total % 2 == 0:
            message += f", 双\n"
        else:
            message += f", 单\n"
        
        message += f"\n 参与人数：{len(set(bet.user_id for bet in game.bets))} 人\n\n"
        
        # 按用户统计总盈亏
        user_profits = {}
        for info in settlement_info:
            user_id = info["user_id"]
            profit = info["profit"]
            if user_id not in user_profits:
                user_profits[user_id] = 0
            user_profits[user_id] += profit
        
        # 分别统计盈利和亏损的玩家
        winners = []
        losers = []
        break_even = []  # 新增：持平的玩家
        for user_id, total_profit in user_profits.items():
            user = self.user_repo.get_by_id(user_id)
            nickname = user.nickname if user and user.nickname else user_id
            
            if total_profit > 0:
                winners.append((nickname, total_profit))
            elif total_profit < 0:
                losers.append((nickname, total_profit))
            else:  # total_profit == 0
                break_even.append(nickname)
        
        # 显示结果
        if winners:
            message += f"🎉 中奖玩家：\n"
            for nickname, profit in winners:
                message += f"• {nickname}: +{int(profit):,} 金币\n"
        
        if losers:
            if winners:
                message += f"\n"
            message += f"💸 未中奖玩家：\n"
            for nickname, loss in losers:
                message += f"• {nickname}: {int(loss):,} 金币\n"
        
        if break_even:
            if winners or losers:
                message += f"\n"
            message += f"⚖️ 持平玩家：\n"
            for nickname in break_even:
                message += f"• {nickname}: ±0 金币\n"
        
        if not winners and not losers and not break_even:
            message += f"🤔 本局无人参与\n"
        
        logger.info(f"骰宝游戏结算完成: {game.game_id}, 结果: {dice}, 总派彩: {total_payout}")
        
        return {
            "success": True,
            "message": message,
            "dice": dice,
            "total": total,
            "settlement": settlement_info
        }
    
    def _normalize_bet_type(self, bet_type: str) -> Optional[str]:
        """标准化下注类型"""
        # 移除常见前缀
        if bet_type.startswith("押"):
            bet_type = bet_type[1:]
        elif bet_type.startswith("鸭"):
            bet_type = bet_type[1:]
        
        # 标准化映射
        mapping = {
            # 大小单双
            "大": "大", "小": "小", "单": "单", "双": "双",
            "奇": "单", "偶": "双",
            
            # 豹子
            "豹子": "豹子", "三同": "豹子", "围骰": "豹子",
            
            # 点数
            "一点": "一点", "二点": "二点", "三点": "三点",
            "四点": "四点", "五点": "五点", "六点": "六点",
            "1点": "一点", "2点": "二点", "3点": "三点",
            "4点": "四点", "5点": "五点", "6点": "六点",
            
            # 总点数
            "4点": "4点", "5点": "5点", "6点": "6点", "7点": "7点",
            "8点": "8点", "9点": "9点", "10点": "10点", "11点": "11点",
            "12点": "12点", "13点": "13点", "14点": "14点", "15点": "15点",
            "16点": "16点", "17点": "17点"
        }
        
        return mapping.get(bet_type)
    
    def _get_odds(self, bet_type: str) -> float:
        """获取赔率"""
        if bet_type in ["一点", "二点", "三点", "四点", "五点", "六点"]:
            return 1.0  # 基础赔率，实际根据出现次数动态计算
        
        return self.odds_table.get(bet_type, 1.0)
    
    def _analyze_dice_result(self, dice: List[int], total: int) -> Dict[str, Any]:
        """分析骰子结果"""
        return {
            "dice": dice,
            "total": total,
            "is_big": total >= 11,
            "is_small": total <= 10,
            "is_odd": total % 2 == 1,
            "is_even": total % 2 == 0,
            "is_triple": dice[0] == dice[1] == dice[2],
            "point_counts": {i: dice.count(i) for i in range(1, 7)}
        }
    
    def _check_bet_win(self, bet: SicboBet, results: Dict[str, Any]) -> bool:
        """检查下注是否中奖"""
        bet_type = bet.bet_type
        
        # 豹子特殊规则：豹子出现时，大小单双全输
        if results["is_triple"]:
            if bet_type == "豹子":
                return True
            elif bet_type in ["大", "小", "单", "双"]:
                return False
        
        # 大小单双
        if bet_type == "大":
            return results["is_big"] and not results["is_triple"]
        elif bet_type == "小":
            return results["is_small"] and not results["is_triple"]
        elif bet_type == "单":
            return results["is_odd"] and not results["is_triple"]
        elif bet_type == "双":
            return results["is_even"] and not results["is_triple"]
        
        # 豹子
        elif bet_type == "豹子":
            return results["is_triple"]
        
        # 点数
        elif bet_type in ["一点", "二点", "三点", "四点", "五点", "六点"]:
            point_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}
            point = point_map.get(bet_type[0], 0)
            return results["point_counts"].get(point, 0) > 0
        
        # 总点数
        elif bet_type.endswith("点") and bet_type[:-1].isdigit():
            target_total = int(bet_type[:-1])
            return results["total"] == target_total
        
        return False
    
    def get_user_bets(self, user_id: str, session_id: str) -> Dict[str, Any]:
        """获取用户在当前游戏的下注情况"""
        game = self.games.get(session_id)
        if not game or not game.is_active:
            return {
                "success": False,
                "message": "当前会话没有进行中的游戏"
            }
        
        user_bets = [bet for bet in game.bets if bet.user_id == user_id]
        
        # 转换为字典格式供图片生成使用
        bet_list = []
        for bet in user_bets:
            bet_list.append({
                "bet_type": bet.bet_type,
                "amount": bet.amount,
                "odds": bet.odds
            })
        
        total_bet = sum(bet.amount for bet in user_bets)
        
        return {
            "success": True,
            "bets": bet_list,
            "total_bet": total_bet
        }
