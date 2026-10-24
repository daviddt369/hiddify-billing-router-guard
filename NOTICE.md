# NOTICE: what this stack is built on / На чём построен этот стек

This repository installs a panel ("Cheburnator" by default) that is **built on Hiddify**. It is a personal, non-commercial project: the parts below keep their own licences and their authors stay credited.
Репозиторий ставит панель («Чебурнатор» по умолчанию) на основе **Hiddify**. Это личный некоммерческий проект: чужие части остаются под своими лицензиями, авторы Hiddify указаны.

| Part | Source | Licence |
|---|---|---|
| Hiddify Manager (installer, templates, scripts) | https://github.com/hiddify/Hiddify-Manager (pinned `v12.0.0`) | GPL-3.0 |
| Hiddify Panel (the Python web panel, `hiddifypanel`) | https://github.com/hiddify/Hiddify-Panel (commit `56f902d`) | **CC BY-NC-SA 4.0** (attribution, non-commercial, share-alike) |
| Admin V2 dashboard (`vendor/admin-v2.tar.gz`, a build of the Hiddify 13 admin app) | Hiddify project | follows the Hiddify Panel licence |
| Xray-core, the geo data files, HAProxy, WireGuard, nginx and the rest | installed from their own releases / packages, pinned and checked by sha256 where the installer downloads them (`golden/pins.env`) | each under its own licence |

What this repository adds (the billing / routing / anti-share / multi-location layer, the monitoring, the installer, the overlay) is under the MIT licence of `LICENSE`, **except** the files in `golden/overlay/` that are modified copies of Hiddify Panel files: those stay under CC BY-NC-SA 4.0 and must not be used commercially.

Practical meaning: install it for yourself and for people you know, change it, and keep this notice. The Hiddify Panel licence allows non-commercial use only; whether a particular use counts as commercial is for you to judge, and for anything in doubt ask the Hiddify authors first. This file is not legal advice.

The name "Hiddify" is the name of that project; "Cheburnator" is only the name this installation shows in its interface (the `PRODUCT_NAME` parameter).
