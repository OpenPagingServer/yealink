import html


PUSH_TABLE = "endpoints-output-yealink-push"


def h(value):
    return html.escape("" if value is None else str(value), quote=True)


def forms():
    return {
        "push": {
            "label": "Yealink Push XML",
            "description": "Send visual messages to Yealink IP Phones",
        },
    }


def ensure_schema(conn_factory):
    conn = conn_factory()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS `{PUSH_TABLE}` ("
                "`ipv4` VARCHAR(45) NOT NULL, "
                "`name` VARCHAR(255) NOT NULL DEFAULT '', "
                "`status` VARCHAR(32) NOT NULL DEFAULT 'Unchecked', "
                "`username` VARCHAR(255) NOT NULL DEFAULT '', "
                "`password` VARCHAR(255) NOT NULL DEFAULT '', "
                "PRIMARY KEY (`ipv4`), KEY `status_idx` (`status`)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci"
            )
            cur.execute(f"SHOW COLUMNS FROM `{PUSH_TABLE}` LIKE 'name'")
            if not cur.fetchone():
                cur.execute(
                    f"ALTER TABLE `{PUSH_TABLE}` ADD COLUMN `name` VARCHAR(255) NOT NULL DEFAULT '' AFTER `ipv4`"
                )
        conn.commit()
    finally:
        conn.close()


def query_one(conn_factory, sql, params=()):
    conn = conn_factory()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    finally:
        conn.close()


def execute(conn_factory, sql, params=()):
    conn = conn_factory()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def module_body(content):
    return (
        "<style>body{font-family:Tahoma,sans-serif;margin:0;padding:18px;color:#202124;background:#fff}.grid{display:grid;gap:12px}.row{display:grid;gap:6px}"
        "label{font-weight:500}.check{display:flex;align-items:center;gap:8px;font-weight:400}"
        ".control{padding:10px;border:1px solid #ddd;border-radius:4px;font:inherit}.button,button{background:#1976D2;color:#fff;border:0;border-radius:4px;padding:10px 14px;font:inherit;cursor:pointer}"
        ".danger{background:#c62828}.success{background:#e8f5e9;border:1px solid #a5d6a7;color:#1b5e20;padding:10px;border-radius:6px;margin-bottom:12px}"
        ".error{background:#ffebee;border:1px solid #ef9a9a;color:#b71c1c;padding:10px;border-radius:6px;margin-bottom:12px}"
        ".warn{background:#fff8e1;border:1px solid #ffe082;color:#5d4037;padding:12px;border-radius:6px;margin-bottom:12px}.meta{color:#5f6368;margin:0 0 14px}"
        "@media(prefers-color-scheme:dark){body{background:#1e1e1e;color:#e0e0e0}.control{background:#171717;border-color:#333;color:#eee}.button,button{background:#BB86FC;color:#000}.meta{color:#aaa}.warn{background:#352b10;border-color:#66511a;color:#ffe2a8}}</style>"
        + content
    )


def alert(message, error):
    out = ""
    if message:
        out += f'<div class="success">{h(message)}</div>'
    if error:
        out += f'<div class="error">{h(error)}</div>'
    return out


def normalized_device_name(row):
    device_name = str((row or {}).get("name") or "").strip()
    username = str((row or {}).get("username") or "").strip()
    if device_name and username and device_name == username:
        return ""
    return device_name


def render_form(form_type, request, conn_factory, page, user):
    ensure_schema(conn_factory)
    if form_type not in forms():
        return page("Endpoint Form", module_body("<h1>Endpoint form not found</h1>"), "endpoints", user, status=404)
    message = ""
    error = ""
    values = {"ipv4": "", "name": "", "username": "", "password": "", "unchecked": ""}

    if request.method == "POST":
        try:
            for key in values:
                values[key] = str(request.form.get(key, values[key]) or "").strip()
            values["unchecked"] = "1" if request.form.get("unchecked") else ""
            if not values["ipv4"]:
                raise ValueError("IPv4 address is required.")
            if not values["name"]:
                raise ValueError("Device name is required.")
            if query_one(conn_factory, f"SELECT ipv4 FROM `{PUSH_TABLE}` WHERE ipv4=%s", (values["ipv4"],)):
                raise ValueError("That Yealink push endpoint already exists.")
            status = "Unchecked" if values["unchecked"] else "New"
            execute(
                conn_factory,
                f"INSERT INTO `{PUSH_TABLE}` (ipv4, name, status, username, password) VALUES (%s,%s,%s,%s,%s)",
                (values["ipv4"], values["name"], status, values["username"], values["password"]),
            )
            message = "Yealink push endpoint added."
            values = {"ipv4": "", "name": "", "username": "", "password": "", "unchecked": ""}
        except Exception as exc:
            error = str(exc)

    body = (
        f"{alert(message, error)}<form method='post' class='grid'>"
        f"<div class='row'><label>IPv4 Address</label><input class='control' name='ipv4' value='{h(values['ipv4'])}' required></div>"
        f"<div class='row'><label>Name</label><input class='control' name='name' value='{h(values['name'])}' required></div>"
        f"<div class='row'><label>Username</label><input class='control' name='username' value='{h(values['username'])}'></div>"
        f"<div class='row'><label>Password</label><input class='control' type='password' name='password' value='{h(values['password'])}'></div>"
        f"<label class='check'><input type='checkbox' name='unchecked' value='1' {'checked' if values.get('unchecked') else ''}> Do not check status</label>"
        "<button class='button' type='submit'>Add Yealink Push Endpoint</button></form>"
    )
    return page(forms()[form_type]["label"], module_body(body), "endpoints", user)


def render_action(action, endpoint_id, request, conn_factory, page, user):
    ensure_schema(conn_factory)
    message = ""
    error = ""
    row = None
    try:
        if not str(endpoint_id).startswith("push-"):
            raise ValueError("Unknown Yealink endpoint type.")
        ipv4 = str(endpoint_id)[5:]
        lookup = str(request.form.get("_lookup_ipv4", ipv4) or "").strip()
        row = query_one(conn_factory, f"SELECT ipv4, name, status, username, password FROM `{PUSH_TABLE}` WHERE ipv4=%s", (lookup,))
        if not row:
            raise ValueError("Endpoint not found.")
        label = f"{normalized_device_name(row) or 'Yealink Push'} ({row.get('ipv4')})"
        if request.method == "POST":
            if action == "delete":
                execute(conn_factory, f"DELETE FROM `{PUSH_TABLE}` WHERE ipv4=%s", (row["ipv4"],))
                return page("Endpoint Deleted", module_body("<script>window.top.location.href='/admin/manage-endpoints'</script><div class='success'>Yealink push endpoint deleted.</div>"), "endpoints", user)
            new_ipv4 = str(request.form.get("ipv4", "") or "").strip()
            name = str(request.form.get("name", "") or "").strip()
            username = str(request.form.get("username", "") or "").strip()
            password = str(request.form.get("password", "") or "").strip()
            if not new_ipv4:
                raise ValueError("IPv4 address is required.")
            if not name:
                raise ValueError("Device name is required.")
            duplicate = query_one(conn_factory, f"SELECT ipv4 FROM `{PUSH_TABLE}` WHERE ipv4=%s AND ipv4<>%s", (new_ipv4, row["ipv4"]))
            if duplicate:
                raise ValueError("That Yealink push endpoint already exists.")
            execute(conn_factory, f"UPDATE `{PUSH_TABLE}` SET ipv4=%s, name=%s, username=%s, password=%s WHERE ipv4=%s", (new_ipv4, name, username, password, row["ipv4"]))
            return page("Endpoint Saved", module_body("<script>window.top.location.href='/admin/manage-endpoints'</script><div class='success'>Yealink push endpoint updated.</div>"), "endpoints", user)
    except Exception as exc:
        error = str(exc)
        label = endpoint_id

    if action == "delete":
        body = f"{alert(message, error)}"
        if row:
            body += f"<div class='warn'>Delete {h(label)}?</div><form method='post'><button class='button danger' type='submit'>Delete Endpoint</button></form>"
        return page("Delete Yealink Endpoint", module_body(body), "endpoints", user)

    if not row:
        return page("Edit Yealink Endpoint", module_body(alert(message, error)), "endpoints", user)
    body = (
        f"{alert(message, error)}<p class='meta'>Current status: {h(row.get('status'))}</p><form method='post' class='grid'>"
        f"<input type='hidden' name='_lookup_ipv4' value='{h(row.get('ipv4'))}'>"
        f"<div class='row'><label>IPv4 Address</label><input class='control' name='ipv4' value='{h(row.get('ipv4'))}' required></div>"
        f"<div class='row'><label>Name</label><input class='control' name='name' value='{h(normalized_device_name(row))}' required></div>"
        f"<div class='row'><label>Username</label><input class='control' name='username' value='{h(row.get('username'))}'></div>"
        f"<div class='row'><label>Password</label><input class='control' type='password' name='password' value='{h(row.get('password'))}'></div>"
        "<button class='button' type='submit'>Save Yealink Push Endpoint</button></form>"
    )
    return page("Edit Yealink Endpoint", module_body(body), "endpoints", user)
