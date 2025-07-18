from flask import Flask, render_template, request
from dotenv import load_dotenv
import os

load_dotenv()
app = Flask(__name__)

def read_env_vars():
    keys = ['MQTT_ENABLED', 'MQTT_HOST', 'MQTT_PORT', 'PROXY_TARGET_IP', 'PROXY_TARGET_PORT']
    return {key: os.getenv(key, '-') for key in keys}

@app.route("/env", methods=["GET", "POST"])
def show_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if request.method == "POST":
        with open(env_path, "r") as f:
            lines = f.readlines()
        new_lines = []
        for line in lines:
            key = line.split("=")[0].strip()
            if key in request.form and key != "UI_PASS":
                new_lines.append(f"{key}={request.form[key]}
")
            else:
                new_lines.append(line)
        with open(env_path, "w") as f:
            f.writelines(new_lines)
        load_dotenv(override=True)
    values = read_env_vars()
    return render_template("env.html", values=values)
