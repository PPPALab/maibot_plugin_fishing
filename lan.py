"""Explicit loopback/RFC1918/ULA boundaries; never trust forwarded headers."""
import ipaddress
import re

LOCAL_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    '127.0.0.0/8', '::1/128', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7',
))


def is_local_address(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return any(address in network for network in LOCAL_NETWORKS if network.version == address.version)


def normalize_allowed_hosts(hosts):
    if not hosts:
        raise ValueError('后台允许访问的主机不能为空')
    normalized = []
    for host in hosts:
        host = host.strip().lower()
        try:
            address = ipaddress.ip_address(host.strip('[]'))
        except ValueError:
            # Exact names only: no schemes, paths, ports, wildcard/subdomain rules.
            if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*', host):
                raise ValueError('后台主机只填写局域网IP或完整主机名，不含协议、端口、路径或通配符')
        else:
            if not is_local_address(str(address)):
                raise ValueError('后台主机IP只允许回环地址或RFC1918/ULA局域网地址')
            host = f'[{address.compressed}]' if address.version == 6 else address.compressed
        if host not in normalized:
            normalized.append(host)
    return normalized
