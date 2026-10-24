# client-profiles: Happ / INCY routing and per-app bypass

What the dedicated `/happ/` subscription link serves to Happ and INCY (the layer file is `../multiloc-layer/multiloc.py`; these are the diffs of the three steps and helpers).

| step | problem | fix |
|---|---|---|
| `cp12-ru-zone-domain` | INCY took the `regexp:.*\.ru$` entry of the direct list into its DNS rules only; Russian sites that were not on the whitelist went into the tunnel. Happ applied it correctly. | The zone is now also written as `domain:ru`, `domain:su`, `domain:xn--p1ai` (first), the `regexp:` entries stay: Happ and the DNS rules behave as before. |
| `cp13-app-bypass-headers` | Apps that refuse to work while the system reports a VPN (banks, state services, messengers) forced users to switch the VPN off. | Android only: `per-app-proxy-mode: bypass` and `per-app-proxy-list: a,b,c` response headers, from the settings key `bypass_apps` (and `provider_id` for Happ). Added only to the responses that carry the routing rules, only for Happ / INCY; an empty list adds nothing. |
| `cp14-incy-per-app-enable` | INCY switches its per-app mode on with `per-app-proxy-enable: 1` and needs no provider id. | Each client gets only the headers its own documentation lists. |

* Settings file: `.../hiddify-panel/var/multiloc-roscom.json`; set with `multiloc.roscom_set(bypass_apps=[...], provider_id="...")` (both are validated).
* `cp13-app-bypass-headers/check_packages.py` checks Android package names against the RuStore catalog slowly (the catalog rate-limits); `bypass-apps.example.txt` is a checked example list.
* `make_pkg.py` builds the one-file delta package for the installer's delta-deploy tool (`../multiloc-layer/multiloc_delta_deploy.py`): dry run, apply with backup, automatic restore on failure.
* Header sources: Happ https://www.happ.su/main/dev-docs/app-management and https://www.happ.su/main/dev-docs/provider-id, INCY https://incy.gitbook.io/docs/docs-en/app-management.en
