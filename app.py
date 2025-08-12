from flask import Flask, render_template, request, redirect, url_for, session, flash
from dotenv import load_dotenv
import os
from auth import login_required, check_credentials
from monitor import get_system_info, get_services_status, restart_service_safe, get_multi_ping_stats, get_all_vnstat_stats, get_iperf_test

# načti .env ze stejného adresáře
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH)

app = Flask(__name__)
app.secret_key = os.getenv("UI_SECRET", "change-me")

@app.route("/", methods=["GET"])
@login_required
def index():
    info = get_system_info()
    services = get_services_status()
    # Nové: získání síťových statistik
    ping_stats = get_multi_ping_stats()
    vnstat_stats = get_all_vnstat_stats()
    return render_template("index.html", info=info, services=services,ping_stats=ping_stats,
        vnstat_stats=vnstat_stats)

@app.route("/restart/<service>", methods=["POST"])
@login_required
def restart(service):
    ok, msg = restart_service_safe(service)
    flash(msg, "success" if ok else "error")
    return redirect(url_for("index"))

@app.route("/env", methods=["GET", "POST"])
@login_required
def show_env():
    if request.method == "POST":
        # upravíme .env – přepíšeme pouze známé klíče
        allowed = ['MQTT_ENABLED', 'MQTT_HOST', 'MQTT_PORT',
                   'MQTT_TOPIC_PREFIX', 'MQTT_REPORT_INTERVAL', 
                   'PROXY_TARGET_IP', 'PROXY_TARGET_PORT',
                   'UI_USER', 'UI_PASS', 'UI_SECRET', 'PORT']
        # načti původní
        with open(ENV_PATH, "r") as f:
            lines = f.readlines()
        new_lines = []
        for line in lines:
            if "=" not in line or line.strip().startswith("#"):
                new_lines.append(line)
                continue
            key = line.split("=", 1)[0].strip()
            if key in allowed:
                value = request.form.get(key, os.getenv(key, ""))
                new_lines.append(f"{key}={value}\n")
            else:
                new_lines.append(line)

        with open(ENV_PATH, "w") as f:
            f.writelines(new_lines)

        load_dotenv(dotenv_path=ENV_PATH, override=True)
        flash(".env uloženo", "success")
        return redirect(url_for("show_env"))

    keys = ['MQTT_ENABLED', 'MQTT_HOST', 'MQTT_PORT',
            'MQTT_TOPIC_PREFIX', 'MQTT_REPORT_INTERVAL', 
            'PROXY_TARGET_IP', 'PROXY_TARGET_PORT',
            'UI_USER', 'UI_PASS', 'UI_SECRET', 'PORT']
    values = {key: os.getenv(key, '') for key in keys}
    return render_template("env.html", values=values)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if check_credentials(request.form.get("username"), request.form.get("password")):
            session["authenticated"] = True
            return redirect(url_for("index"))
        flash("Neplatné přihlašovací údaje", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/network", methods=["GET", "POST"])
@login_required
def network():
    ping_results = []
    iperf_result = None
    default_targets = "8.8.8.8, 192.168.1.1, 192.168.1.9, 192.168.1.10, 192.168.1.20"

    if request.method == "POST":
        action = request.form.get("action")

        if action == "ping":
            targets = request.form.get("targets", default_targets)
            ip_list = [ip.strip() for ip in targets.split(",") if ip.strip()]
            ping_results = get_multi_ping_stats(ip_list)

        elif action == "iperf":
            iperf_ip = request.form.get("iperf_ip", "192.168.1.20")
            duration = int(request.form.get("duration", 10))
            iperf_result = get_iperf_test(iperf_ip, duration)

    return render_template(
        "network.html",
        ping_results=ping_results,
        iperf_result=iperf_result,
        default_targets=default_targets
    )

if __name__ == "__main__":
    # pro vývoj; v produkci běží přes systemd
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))


