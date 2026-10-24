"""CP-18: one collapsed 'Инструкция' panel per page (current texts) instead of the scattered alerts / small notes, the router log folded away, the bot-text editor on the Telegram page.
usage: make_templates.py <dir with the live templates copied from the stand (tpl-stand-copy)> <out dir>"""
import sys
from pathlib import Path

src, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
IMPORT = "{% from 'pt-faq.html' import faq, acc %}\n"


def rd(name):
    return (src / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def wr(name, s):
    (out / name).write_text(s, encoding="utf-8", newline="\n")


def rep(s, old, new, count=1):
    assert s.count(old) == count, f"{s.count(old)}x (want {count}): {old[:80]!r}"
    return s.replace(old, new)


def cut(s, start, end, new=""):
    """replace everything from `start` through the first `end` after it"""
    a = s.index(start)
    b = s.index(end, a) + len(end)
    return s[:a] + new + s[b:]


def after_extends(s, extra):
    first_nl = s.index("\n") + 1
    return s[:first_nl] + extra + s[first_nl:]


# ------------------------------------------------------------------------------------------------------------------ apply bar: status line, the log folded away
s = rd("routing-apply-bar.html")
s = rep(s, '    <pre id="apply-log" class="small bg-light p-2 mt-2 mb-0" style="max-height:14em;overflow:auto;display:none"></pre>\n',
        '    <details id="apply-log-box" class="mt-2" style="display:none"><summary class="small" style="cursor:pointer">Журнал применения (для диагностики)</summary>\n'
        '      <pre id="apply-log" class="small p-2 mt-1 mb-0" style="max-height:14em;overflow:auto"></pre></details>\n')
s = rep(s, "    if (s.summary && s.state !== 'running') t.push(s.summary);\n",
        "    if (s.summary && (s.state === 'failed' || s.state === 'aborted' || s.state === 'needs-confirm')) t.push(s.summary);      // the usual 'all fine' summary is technical English: not shown\n")
s = rep(s, "    if (s.log_tail && s.log_tail.length) { l.style.display = ''; l.textContent = s.log_tail.join('\\n'); l.scrollTop = l.scrollHeight; }\n",
        "    var lb = document.getElementById('apply-log-box');\n"
        "    if (s.log_tail && s.log_tail.length) { lb.style.display = ''; l.textContent = s.log_tail.join('\\n'); l.scrollTop = l.scrollHeight; if (s.state === 'failed' || s.state === 'aborted') lb.open = true; }\n")
wr("routing-apply-bar.html", s)

# ------------------------------------------------------------------------------------------------------------------ external nodes
s = rd("routing-upstream.html")
s = after_extends(s, IMPORT)
s = cut(s, '          <div class="alert alert-info mb-3">\n            <strong>Как работают внешние ноды:</strong>', "</div>\n",
        """          {% call faq() %}
            <p>Внешняя нода — сервер за пределами РФ (VLESS, Trojan или WireGuard). Через внешние ноды идёт весь нероссийский трафик пользователей; российские сайты (домены .ru / .su / .рф, geoip:ru и ваши правила) router отдаёт напрямую с этой ноды.</p>
            <h6>Как router выбирает ноду</h6>
            <ul>
              <li>Каждой ноде на странице «Локации» назначается страна. Внутри страны router сам берёт самую быструю включённую ноду по пингу; сколько мс разницы допустимо, чтобы он не переключался зря, задаётся ниже в «Проверке нод».</li>
              <li>Если все ноды страны недоступны, трафик не уходит напрямую и не переключается на другую страну — соединение закрывается (так не светится российский адрес).</li>
              <li>Нода без страны участвует только в AUTO и в старых профилях.</li>
            </ul>
            <h6>Что на странице</h6>
            <ul>
              <li><b>Health</b> — результат проверки ноды: online, degraded или недоступна; «★ Лучшая» — нода с самым низким пингом среди живых.</li>
              <li>Выключенная нода router не используется, но остаётся в списке.</li>
              <li>Ноды сохраняются сразу; на router они попадают после кнопки «Применить» ниже — панель сама решает, нужен ли перезапуск (обычно секунды).</li>
            </ul>
          {% endcall %}
""")
s = cut(s, '          <div class="alert alert-info mb-3">\n            <strong>Что такое внешняя нода (upstream)?</strong>', "</div>\n",
        """          {% call faq() %}
            <p>Внешняя нода — сервер за пределами РФ, через который router пропускает нероссийский трафик. Поддерживаются VLESS, Trojan и WireGuard.</p>
            <ul>
              <li>Метка и описание нужны только вам; «Включена» — выключенную ноду router не использует.</li>
              <li>Тип туннеля определяет, какие поля нужны ниже (адрес и порт для VLESS и Trojan, endpoint и ключи для WireGuard).</li>
              <li>«Тестовый blackhole» сбрасывает трафик: нужен только чтобы проверить router без реальной ноды, на рабочей панели не используйте.</li>
              <li>Страну ноды задаёт страница «Локации»; после сохранения нажмите «Применить» на странице «Внешние ноды».</li>
            </ul>
          {% endcall %}
""")
wr("routing-upstream.html", s)

# ------------------------------------------------------------------------------------------------------------------ locations
s = rd("routing-location.html")
s = after_extends(s, IMPORT)
s = cut(s, '        <small class="text-muted d-block mb-3">Названия, видимость и порядок стран', "</small>\n")
s = cut(s, '        <div class="alert alert-secondary py-2 mb-3">\n          Маршрутизация RoscomVPN, свои домены', "</div>\n")
s = cut(s, '        <div class="alert alert-info mb-3">\n          <strong>Как это работает.</strong>', "</div>\n",
        """        {% call faq() %}
          <p>Страны (слоты) — это кнопки в приложении Happ / INCY: AUTO и по одной кнопке на страну. Клиент выбирает страну одной кнопкой; трафик всегда идёт через РФ-ноду, российские адреса остаются в России, остальное уходит через ноды выбранной страны.</p>
          <h6>Как работают кнопки</h6>
          <ul>
            <li><b>AUTO</b> — самый быстрый выход по пингу среди всех нод.</li>
            <li><b>Страна</b> — только ноды этой страны, внутри самая быстрая по пингу. Если все ноды страны недоступны, страна не переключается на другую: соединение закрывается.</li>
            <li>Для Happ и INCY есть отдельная ссылка <code>…/happ/</code> (её отдаёт бот тестерам). Обычная ссылка пользователя не меняется ни для кого: Hiddify, v2RayTun и другие приложения работают как раньше.</li>
            <li>Профиль маршрутизации RoscomVPN, свои домены и список приложений вне VPN настраиваются на странице <a href="{{ happ_url }}">Happ / INCY</a>.</li>
          </ul>
          <h6>Слоты</h6>
          <ul>
            <li>Слотов четыре плюс AUTO: у каждого свои входы на сервере, поэтому их число задаётся установкой, а не панелью. «Добавить» — заполнить свободный слот (название, страна, код, галочка «включена»). Чтобы удалить, поставьте «очистить»: слот освободится вместе с нодами. Страна без названия не показывается в подписке, даже если включена.</li>
            <li><b>Видимость</b>: «testers» — только пользователям из списка тестовых; «all» — всем активным; «hidden» — никому.</li>
            <li>Слоты и назначения нод сохраняются одной кнопкой «Сохранить всё»; если в одной строке ошибка, не сохраняется ничего.</li>
          </ul>
          <h6>Что нужно применять</h6>
          <p>Название, видимость и порядок меняются в подписке сразу. Назначение нод и включение страны попадают на router после кнопки «Применить» выше: панель сама решает, нужен ли перезапуск (обычно секунды).</p>
        {% endcall %}
""")
s = cut(s, '        <small class="text-muted">Слотов четыре плюс AUTO:', "</small>\n")
wr("routing-location.html", s)

# ------------------------------------------------------------------------------------------------------------------ rule sources
s = rd("routing-rule-source.html")
s = after_extends(s, IMPORT)
s = cut(s, '<p class="text-muted small">\n  Каждый источник хранит список доменов', "</p>\n",
        """{% call faq() %}
  <p>Источник — список доменов или подсетей с политикой: напрямую, через внешнюю ноду или блокировать. Router использует правила всех включённых источников.</p>
  <ul>
    <li><b>Re-import</b> — удаляет прежние правила источника и загружает свежие из URL, файла или текста.</li>
    <li><b>Выключить</b> — отключает правила источника в конфиге, не удаляя их; «Включить» возвращает обратно.</li>
    <li><b>Удалить</b> — удаляет источник и все его правила безвозвратно.</li>
    <li>Изменения сохраняются сразу; на router они попадают после кнопки «Применить» выше — панель сама решает, нужен ли перезапуск.</li>
    <li>Пример готового списка — itdoginfo/allow-domains: <code>…/Russia/inside-raw.lst</code> (домены) и <code>…/Russia/inside-cidr.lst</code> (подсети).</li>
  </ul>
{% endcall %}
""")
wr("routing-rule-source.html", s)

# ------------------------------------------------------------------------------------------------------------------ Happ / INCY
s = rd("routing-happ.html")
s = after_extends(s, IMPORT)
s = cut(s, '        <small class="text-muted d-block mb-3">Всё на этой странице относится только', "</small>\n",
        """        {% call faq() %}
          <p>Всё на этой странице относится только к отдельной ссылке <code>…/happ/</code> для приложений Happ и INCY. Обычная ссылка пользователя и другие приложения не меняются. Перезапускать router не нужно: изменения подхватываются при следующем обновлении подписки в приложении.</p>
          <h6>Разделы</h6>
          <ul>
            <li><b>Маршрутизация RoscomVPN</b> — готовый профиль «что напрямую, что через VPN» для пользователей из России. Можно вставить свой профиль <code>happ://routing/onadd/…</code>.</li>
            <li><b>Свои домены</b> — добавляются в начало списков профиля: напрямую (с устройства, без VPN) или через VPN.</li>
            <li><b>Приложения вне VPN (Android)</b> — пакеты, которые Android не пускает в туннель (банки, Госуслуги, домофон и т. п.). Для Happ нужен Provider ID из кабинета happ-proxy.com, для INCY он не нужен.</li>
          </ul>
        {% endcall %}
""")
wr("routing-happ.html", s)

# ------------------------------------------------------------------------------------------------------------------ anti-share
s = (src.parent / "ru-copy" / "anti-share-settings.html").read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10))      # production has a newer one than the stand (CDN addresses, clear-all): edit THAT
first_nl = s.index("\n") + 1
s = s[:first_nl] + IMPORT + s[first_nl:]
s = rep(s, "<h1>Anti-share</h1>", "<h1>Анти-шаринг</h1>")
s = cut(s, '  <p class="text-muted">Отдельная политика anti-share поверх baseline 12.4.', "</p>\n",
        """  {% call faq() %}
    <p>Анти-шаринг следит, чтобы одной подпиской не пользовались одновременно с слишком многих адресов. Лимит берётся из поля «макс. устройств» пользователя или тарифа.</p>
    <h6>Как это работает</h6>
    <ul>
      <li>Раз в цикл панель считает уникальные IP каждого пользователя за «Окно учёта IP». Адреса, которыми пользователь давно и регулярно пользуется, получают доверие и не штрафуются; адреса CDN считаются одним слотом.</li>
      <li>Превышение лимита копит очки подозрительности. Пороги: suspect — подозрение, warned — предупреждение в Telegram, blocked — лишние IP временно блокируются на сервере (nftables) на «Время бана».</li>
      <li>Первые «Дни обучения» профиль только набирает историю, санкций нет.</li>
      <li>Ниже можно добавить общие адреса CDN, которые не считаются устройствами, и при необходимости очистить всю статистику (перед очисткой сохраняется копия).</li>
      <li>«Только симуляция блокировок» — всё считается и пишется в журнал, но IP не блокируются: так удобно проверять пороги.</li>
    </ul>
  {% endcall %}
""")
s = rep(s, '<h3 class="box-title">Policy</h3>', '<h3 class="box-title">Политика</h3>')
wr("anti-share-settings.html", s)

# ------------------------------------------------------------------------------------------------------------------ business: routing / telegram / yookassa
s = rd("business-settings.html")
s = after_extends(s, IMPORT)

# the routing section: the instruction in one panel, no alerts in the middle of the form
s = rep(s, "{% if active_section == \"routing\" %}{% set show_apply_button = False %}{% include 'routing-apply-bar.html' %}{% endif %}\n",
        "{% if active_section == \"routing\" %}{% set show_apply_button = False %}{% include 'routing-apply-bar.html' %}\n"
        """          {% call faq() %}
            <p>Трафик пользователей идёт через эту (российскую) ноду. Российские адреса — домены .ru / .su / .рф, geoip:ru и ваши правила ниже — остаются в России и идут напрямую. Всё остальное router отправляет на внешние ноды (см. «Внешние ноды» и «Локации»). Если все ноды выбранной страны недоступны, трафик не уходит напрямую: соединение закрывается.</p>
            <h6>Что делает кнопка «Применить»</h6>
            <p>Сохраняет настройки и применяет их на router. Панель сама решает, нужен ли перезапуск: если ничего не менялось — ничего не происходит; иначе конфиг собирается в фоне и router перезапускается один раз (обычно секунды, соединения через router на это время сбрасываются).</p>
            <h6>Важно знать</h6>
            <ul>
              <li>Правила применяются и к Xray, и к sing-box. Если выключить одно из них, часть протоколов будет ходить мимо router.</li>
              <li>При включённой коммерческой маршрутизации в настройках Hiddify выключите «Блокировать внутренние сайты», иначе маршрутизация работает некорректно.</li>
              <li>UDP/443 — это QUIC / HTTP3. Блокировка делает маршрутизацию предсказуемее: клиенты переходят на TCP/443.</li>
              <li>Свои правила применяются только в router-core; добавлять их в шаблоны Hiddify не нужно.</li>
            </ul>
            <h6>Формат своих правил (по одному в строке)</h6>
            <ul>
              <li><code>domain:example.com</code> — только этот домен</li>
              <li><code>suffix:example.com</code> — домен и все поддомены</li>
              <li><code>wildcard:*.example.com</code> — маска</li>
              <li><code>regex:.*\\.example\\.com$</code> — регулярное выражение</li>
              <li><code>ip:1.2.3.4</code> — один IP; <code>cidr:1.2.3.0/24</code> — подсеть</li>
            </ul>
          {% endcall %}
          {% endif %}
""")
s = cut(s, '            <div class="alert alert-info">Внешняя нода = upstream / global.', "</div>\n")
s = cut(s, '            <div class="alert alert-info">Для полного split-routing', "</div>\n")
s = cut(s, '            <div class="alert alert-warning">UDP/443 управляет QUIC/HTTP3.', "</div>\n")
s = cut(s, '            <div class="alert alert-info">Пользовательские правила текущей ноды применяются только', "</div>\n")
s = cut(s, '            <p class="text-muted">Эти правила оставляют выбранные домены и IP на текущей ноде.', '            <div class="form-group"><label for="{{ form.custom_ru_rules_bulk.id }}">',
        '            <p class="text-muted">Эти правила оставляют выбранные домены и IP на этой ноде (формат — в инструкции вверху страницы). Всё остальное, кроме встроенных суффиксов и geoip:ru, уходит через внешнюю ноду.</p>\n            <div class="form-group"><label for="{{ form.custom_ru_rules_bulk.id }}">')

# telegram
TG = """{% if active_section == "telegram" %}
            <div id="telegram-settings"></div>
            {% set G = {} %}{% for g in bot_text_groups %}{% set _ = G.update({g.key: g}) %}{% endfor %}
            {% macro bot_fields(key) %}{% if key in G %}{% for it in G[key]['items'] %}
            <div class="form-group"><label for="bt-{{ it.slug }}">{{ it.label }}{% if it.changed %} <span class="badge badge-warning">изменён</span>{% endif %}</label>
              <textarea class="form-control" id="bt-{{ it.slug }}" name="bot_text__{{ it.slug }}" rows="{{ it.rows }}">{{ it.value }}</textarea>
              <small class="form-text text-muted">{% if it.placeholders %}Подстановки: {% for p in it.placeholders %}<code>{{ p }}</code> {% endfor %}{% else %}Подстановок в этом тексте нет.{% endif %} Чтобы вернуть стандартный текст, очистите поле.</small></div>
            {% endfor %}{% endif %}{% endmacro %}
            {% call faq() %}
              <p>Здесь настраивается Telegram-бот: подключение, тексты, которые бот присылает пользователям, и напоминания о продлении.</p>
              <h6>Как бот встречает пользователя</h6>
              <ul>
                <li>Пользователь нажимает «Старт» и кнопкой отправляет номер телефона. Если номер есть в панели, аккаунт привязывается к Telegram. Если нет: при автоматической регистрации выдаётся пробный доступ (трафик, срок и число устройств задаются в разделе «Регистрация и пробный доступ»), при ручной бот просит обратиться к администратору.</li>
                <li>После привязки бот присылает «Приветственное сообщение», а затем сообщение со ссылкой на подписку. Все эти тексты редактируются в разделах ниже.</li>
              </ul>
              <h6>Как менять тексты</h6>
              <ul>
                <li>В каждом поле стоит текст, который бот шлёт сейчас. Измените его и нажмите «Сохранить»: новый текст используется со следующего сообщения, перезапуск не нужен.</li>
                <li>Подстановки пишутся в фигурных скобках — например <code>{gb}</code>, <code>{days}</code>, <code>{user_link}</code>: бот заменит их настоящими значениями. Доступные подстановки указаны под каждым полем; других использовать нельзя.</li>
                <li>Приветствие и инструкция понимают HTML Telegram (<code>&lt;b&gt;</code>, <code>&lt;a href="…"&gt;</code>); остальные тексты — обычный текст.</li>
              </ul>
            {% endcall %}
            {% call acc("Подключение бота", open=True) %}
              <div class="form-group"><label for="{{ form.telegram_bot_token.id }}">{{ form.telegram_bot_token.label.text }}</label>{{ form.telegram_bot_token(class_="form-control") }}<small class="form-text text-muted">{{ form.telegram_bot_token.description }}</small></div>
              <div class="form-group"><label for="{{ form.telegram_webhook_domain.id }}">{{ form.telegram_webhook_domain.label.text }}</label>{{ form.telegram_webhook_domain(class_="form-control") }}<small class="form-text text-muted">{{ form.telegram_webhook_domain.description }}</small></div>
              <div class="form-group"><label for="{{ form.support_url.id }}">{{ form.support_url.label.text }}</label>{{ form.support_url(class_="form-control") }}<small class="form-text text-muted">{{ form.support_url.description }}</small></div>
              <div class="form-group form-check">{{ form.telegram_api_proxy_enable(class_="form-check-input") }} <label class="form-check-label" for="{{ form.telegram_api_proxy_enable.id }}">Включить прокси Telegram API</label></div>
              <div class="form-group"><label for="{{ form.telegram_api_proxy_url.id }}">{{ form.telegram_api_proxy_url.label.text }}</label>{{ form.telegram_api_proxy_url(class_="form-control") }}<small class="form-text text-muted">{{ form.telegram_api_proxy_url.description }}</small></div>
            {% endcall %}
            {% call acc("Регистрация и пробный доступ", hint=(G['reg'].hint if 'reg' in G else ''), badge=("изменено: " ~ G['reg'].changed if 'reg' in G and G['reg'].changed else "")) %}
              <div class="form-row">
                <div class="form-group col-md-4"><label for="trial-gb">Пробный доступ: трафик, ГБ</label><input type="text" class="form-control" id="trial-gb" name="trial_gb" value="{{ bot_trial.gb }}" inputmode="decimal"></div>
                <div class="form-group col-md-4"><label for="trial-days">Пробный доступ: срок, дней</label><input type="text" class="form-control" id="trial-days" name="trial_days" value="{{ bot_trial.days }}" inputmode="numeric"></div>
                <div class="form-group col-md-4"><label for="trial-ips">Пробный доступ: устройств</label><input type="text" class="form-control" id="trial-ips" name="trial_max_ips" value="{{ bot_trial.max_ips }}" inputmode="numeric"></div>
              </div>
              <small class="form-text text-muted mb-3">Это настоящие условия пробной подписки: столько трафика, дней и устройств бот выдаёт каждому новому пользователю при автоматической регистрации (по умолчанию 1 ГБ, 2 дня, 1 устройство). Уже выданные пробные доступы не меняются. Те же числа подставляются в текст «Выдан пробный доступ» вместо <code>{gb}</code> и <code>{days}</code>.</small>
              <div class="form-group"><label for="{{ form.telegram_registration_mode.id }}">{{ form.telegram_registration_mode.label.text }}</label>{{ form.telegram_registration_mode(class_="form-control") }}<small class="form-text text-muted">{{ form.telegram_registration_mode.description }}</small></div>
              <div class="form-group"><label for="{{ form.telegram_welcome_message.id }}">{{ form.telegram_welcome_message.label.text }}</label>{{ form.telegram_welcome_message(class_="form-control") }}<small class="form-text text-muted">{{ form.telegram_welcome_message.description }}</small></div>
              {{ bot_fields('reg') }}
            {% endcall %}
            {% call acc("Ссылка на подписку", hint=(G['links'].hint if 'links' in G else ''), badge=("изменено: " ~ G['links'].changed if 'links' in G and G['links'].changed else "")) %}{{ bot_fields('links') }}{% endcall %}
            {% call acc("Тарифы и оплата", hint=(G['pay'].hint if 'pay' in G else ''), badge=("изменено: " ~ G['pay'].changed if 'pay' in G and G['pay'].changed else "")) %}{{ bot_fields('pay') }}{% endcall %}
            {% call acc("Инструкция и напоминания о продлении") %}
              <div class="form-group"><label for="{{ form.telegram_instruction_button_text.id }}">{{ form.telegram_instruction_button_text.label.text }}</label>{{ form.telegram_instruction_button_text(class_="form-control") }}<small class="form-text text-muted">{{ form.telegram_instruction_button_text.description }}</small></div>
              <div class="form-group"><label for="{{ form.telegram_instruction_message.id }}">{{ form.telegram_instruction_message.label.text }}</label>{{ form.telegram_instruction_message(class_="form-control") }}<small class="form-text text-muted">{{ form.telegram_instruction_message.description }}</small></div>
              <div class="form-group"><label for="{{ form.telegram_subscription_expiry_reminder_days.id }}">{{ form.telegram_subscription_expiry_reminder_days.label.text }}</label>{{ form.telegram_subscription_expiry_reminder_days(class_="form-control") }}<small class="form-text text-muted">{{ form.telegram_subscription_expiry_reminder_days.description }}</small></div>
              <div class="form-group"><label for="{{ form.telegram_subscription_expiry_reminder_message.id }}">{{ form.telegram_subscription_expiry_reminder_message.label.text }}</label>{{ form.telegram_subscription_expiry_reminder_message(class_="form-control") }}<small class="form-text text-muted">{{ form.telegram_subscription_expiry_reminder_message.description }}</small></div>
            {% endcall %}
            {% call acc("Ошибки привязки номера", hint=(G['err'].hint if 'err' in G else ''), badge=("изменено: " ~ G['err'].changed if 'err' in G and G['err'].changed else "")) %}{{ bot_fields('err') }}{% endcall %}
            {% call acc("Подсказки и кнопки", hint=(G['misc'].hint if 'misc' in G else ''), badge=("изменено: " ~ G['misc'].changed if 'misc' in G and G['misc'].changed else "")) %}{{ bot_fields('misc') }}{% endcall %}
            """
s = cut(s, '{% if active_section == "telegram" %}\n            <div id="telegram-settings"></div>', '            {% elif active_section == "yookassa" %}', TG + "{% elif active_section == \"yookassa\" %}")
s = cut(s, '            <p class="text-muted">Здесь настраивается прием оплаты через YooKassa для Telegram-бота.</p>\n', "</p>\n",
        """            {% call faq() %}
              <p>Оплата идёт через Telegram Payments с провайдером YooKassa. Пользователь выбирает тариф в боте и платит, не выходя из Telegram; после оплаты бот сам включает тариф.</p>
              <ul>
                <li>Токен берётся в @BotFather: /mybots → ваш бот → Payments → YooKassa. Для проверки есть тестовый токен, для настоящих оплат — боевой.</li>
                <li>Вставьте токен ниже и сохраните. Тарифы с ценой создаются в разделе «Тарифы».</li>
                <li>Без токена оплата не работает: бот предлагает запросить активацию тарифа у администратора.</li>
              </ul>
            {% endcall %}
""")
wr("business-settings.html", s)
print("templates ok:", sorted(p.name for p in out.glob("*.html")))
