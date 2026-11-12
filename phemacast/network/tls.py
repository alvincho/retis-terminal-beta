"""Private-subnet endpoints and exact-certificate TLS verification."""
import asyncio
import hashlib
import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
from urllib.parse import urlsplit

def interface_networks():
    """Actual interface masks, never the assumption that all RFC1918 is local."""
    networks = []
    try:
        import psutil
        for values in psutil.net_if_addrs().values():
            for value in values:
                if value.family in (socket.AF_INET, socket.AF_INET6) and value.netmask:
                    try:
                        mask = value.netmask
                        if value.family == socket.AF_INET6 and ':' in mask:
                            bits = bin(int(ipaddress.ip_address(mask)))[2:].zfill(128)
                            if '01' in bits:continue
                            mask = str(bits.count('1'))
                        networks.append(ipaddress.ip_network(f'{value.address.split("%")[0]}/{mask}', strict=False))
                    except ValueError: pass
        return networks
    except ImportError:
        pass
    try:
        if os.uname().sysname == 'Darwin':
            output = subprocess.run(['/sbin/ifconfig'], capture_output=True, text=True, timeout=3, check=True).stdout
            for address, mask in re.findall(r'inet (\d+\.\d+\.\d+\.\d+) netmask (0x[0-9a-f]+)', output):
                networks.append(ipaddress.ip_network(f'{address}/{ipaddress.IPv4Address(int(mask,16))}',strict=False))
            for address, prefix in re.findall(r'inet6 ([0-9a-f:]+)(?:%[^ ]+)? prefixlen (\d+)', output):
                networks.append(ipaddress.ip_network(f'{address}/{prefix}',strict=False))
        else:
            output = subprocess.run(['ip','-json','address'], capture_output=True, text=True, timeout=3, check=True).stdout
            for interface in json.loads(output):
                for address in interface.get('addr_info',[]):
                    networks.append(ipaddress.ip_network(f"{address['local']}/{address['prefixlen']}",strict=False))
    except (OSError,ValueError,subprocess.SubprocessError):
        pass
    return networks


def lan_endpoint(candidate, networks):
    """Only HTTPS literal private addresses on an attached subnet are eligible."""
    if not isinstance(candidate, dict):
        return None
    try:
        url = urlsplit(candidate.get('url',''))
        address = ipaddress.ip_address(url.hostname)
        private = any(address in ipaddress.ip_network(n) for n in (('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16') if address.version==4 else ('fc00::/7',)))
        pin = candidate.get('tlsFingerprintSha256','').lower()
        if (url.scheme!='https' or not url.port or url.username or url.password or url.path or url.query or url.fragment or
                not private or not re.fullmatch(r'[0-9a-f]{64}', pin) or not any(address.version==n.version and address in n for n in networks)):
            return None
        return {**candidate, 'tlsFingerprintSha256':pin}
    except (ValueError,TypeError):
        return None


async def pinned_context(candidate):
    """Probe without credentials, then trust only that exact leaf for real I/O.

    The actual request performs its own verified TLS handshake; this avoids a
    probe/request race that would exist if verification were merely disabled.
    """
    url = urlsplit(candidate['url'])
    probe = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    probe.check_hostname = False
    probe.verify_mode = ssl.CERT_NONE
    reader, writer = await asyncio.wait_for(asyncio.open_connection(url.hostname,url.port,ssl=probe,server_hostname=url.hostname),2)
    try:
        certificate = writer.get_extra_info('ssl_object').getpeercert(binary_form=True)
    finally:
        writer.close()
        await writer.wait_closed()
    if hashlib.sha256(certificate).hexdigest() != candidate['tlsFingerprintSha256']:
        raise ValueError('Local endpoint certificate does not match the paired resource.')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False  # Literal IP authenticated by the exact approved leaf.
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(certificate))
    context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context

