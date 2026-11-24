"""Ziele für ausgehende Verbindungen (Webhooks, Livestream): nur öffentliche Adressen.

`ipaddress.is_global` allein reicht nicht: IPv6-Adressen können eine IPv4-Adresse enthalten, die je nach Netz
(NAT64, 6to4, Teredo, IPv4-kompatibel/-gemappt) dorthin übersetzt wird – z. B. gilt 64:ff9b::7f00:1 als „global“,
landet über ein NAT64-Gateway aber bei 127.0.0.1. Solche Adressen sind nur erlaubt, wenn auch die eingebettete
IPv4-Adresse öffentlich ist; IPv4-kompatible (::a.b.c.d, veraltet) nie.
"""
import ipaddress

NAT64 = (ipaddress.ip_network('64:ff9b::/96'), ipaddress.ip_network('64:ff9b:1::/48'))


def eingebettet(ip):
    """IPv4-Adressen, die in einer IPv6-Adresse stecken (gemappt, 6to4, Teredo, NAT64)."""
    if ip.version != 6:
        return []
    aus = [x for x in (ip.ipv4_mapped, ip.sixtofour, ip.teredo[1] if ip.teredo else None) if x]
    if any(ip in n for n in NAT64):
        aus.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return aus


def oeffentlich(text):
    """True, wenn die Adresse (Text aus getaddrinfo, ggf. mit %Zone) ein öffentliches Ziel ist."""
    try:
        ip = ipaddress.ip_address(str(text).split('%')[0])
    except ValueError:
        return False
    if ip.version == 6 and int(ip) < 2 ** 32:        # ::a.b.c.d (IPv4-kompatibel), ::, ::1
        return False
    return ip.is_global and all(x.is_global for x in eingebettet(ip))
