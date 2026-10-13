"""hutils/proxy/router_core.py (the commercial routing builder of the router config): `tcp` + `headerType=http` ("Hiddify rawhttp" proxies) was built WITHOUT the http header, so the exit's HAProxy
(which tells the proxies apart by the HTTP request: path / Host) answered 400 and such a node never worked (2026-10-13: the NL trojan node). The same replacement was applied by hand on production
(backup patch5-settings/router_core.py.before); the outbounds of every other node are byte-identical before / after (sha256 of the built outbounds)."""
PKG = "opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel"

ROUTERCORE = {
    f"{PKG}/hutils/proxy/router_core.py": [
        ('''    elif network in {"tcp", "http"}:
        pass

    else:
        raise ValueError(f"Unsupported network type: {network}")
''', '''    elif network in {"tcp", "http"}:
        if network == "tcp" and _first(params, "headerType", "header_type", default="").lower() == "http":
            # Hiddify's "TCP + HTTP header" proxies are told apart by the exit's HAProxy on the HTTP request (path / Host), so the client has to open with one
            request: dict[str, Any] = {"version": "1.1", "method": "GET", "path": [path or "/"]}
            hosts = [h.strip() for h in (host_header or sni).split(",") if h.strip()]
            if hosts:
                request["headers"] = {"Host": hosts}
            stream["tcpSettings"] = {"header": {"type": "http", "request": request}}

    else:
        raise ValueError(f"Unsupported network type: {network}")
'''),
    ],
}
