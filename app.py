from flask import Flask, render_template, request, redirect, url_for
import os
import subprocess
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=env_path)

app = Flask(__name__)

SERVICE_NAMES = {
    "modbus": "modbus_tcp_proxy.service",
    "mqtt": "rpi-mqtt-report.service",
    "ui": "rpi-admin-ui.service"
}

def get_service_status(service):
    try:
        output = subprocess.check_output(["systemctl", "is-active", service]).decode().strip()
        return output
    except:
        return "unknown"

@app.route("/")
def index():
    statuses = {name: get_service_status(unit) for name, unit in SERVICE_NAMES.items()}
    return render_template("status.html", statuses=statuses)

@app.route("/restart/<service_id>", methods=["POST"])
def restart_service(service_id):
    if service_id in SERVICE_NAMES:
        subprocess.call(["sudo", "systemctl", "restart", SERVICE_NAMES[service_id]])
    return redirect(url_for('index'))

@app.route("/env", methods=["GET", "POST"])
def show_env():
    if request.method == "POST":
        with open(env_path, "r") as f:
            lines = f.readlines()
        new_lines = []
        for line in lines:
            if "=" not in line or line.strip().startswith("#"):
                new_lines.append(line)
                continue
            key = line.split("=")[0].strip()
            if key in request.form and key != "UI_PASS":
                new_lines.append(f"{key}={request.form[key]}\n")
            else:
                new_lines.append(line)
        with open(env_path, "w") as f:
            f.writelines(new_lines)
        load_dotenv(dotenv_path=env_path, override=True)
    keys = ['MQTT_ENABLED', 'MQTT_HOST', 'MQTT_PORT', 'PROXY_TARGET_IP', 'PROXY_TARGET_PORT']
    values = {key: os.getenv(key, '-') for key in keys}
    return render_template("env.html", values=values)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
