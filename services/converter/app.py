from flask import Flask, request, send_file, jsonify
import subprocess, os, tempfile

app = Flask(__name__)

ALLOWED_IN = {"docx", "doc", "pdf", "png", "jpg", "jpeg", "webp"}
ALLOWED_OUT = {"pdf", "docx", "png", "jpg"}

LO_FILTERS = {
    ("docx", "pdf"): "writer_pdf_Export",
    ("doc", "pdf"): "writer_pdf_Export",
    ("pdf", "docx"): "writer_pdf_import",
    ("png", "pdf"): "draw_pdf_Export",
    ("jpg", "pdf"): "draw_pdf_Export",
    ("jpeg", "pdf"): "draw_pdf_Export",
    ("webp", "pdf"): "draw_pdf_Export",
    ("pdf", "png"): "draw_png_Export",
    ("pdf", "jpg"): "draw_jpg_Export",
    ("docx", "png"): "draw_png_Export",
    ("docx", "jpg"): "draw_jpg_Export",
}

@app.route("/health")
def health():
    try:
        r = subprocess.run(["libreoffice", "--headless", "--version"],
                         capture_output=True, text=True, timeout=10)
        return jsonify({"status": "ok", "version": r.stdout.strip()})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/convert", methods=["POST"])
def convert():
    if "file" not in request.files:
        return "no file", 400
    file = request.files["file"]
    target = request.form.get("target", "pdf")
    
    src_ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if src_ext not in ALLOWED_IN:
        return f"unsupported source format: {src_ext}", 400
    if target not in ALLOWED_OUT:
        return f"unsupported target format: {target}", 400

    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, f"input.{src_ext}")
        file.save(src_path)

        out_dir = os.path.join(tmpdir, "out")
        os.makedirs(out_dir, exist_ok=True)

        filter_key = (src_ext, target)
        cmd = [
            "libreoffice", "--headless", "--norestore",
            "--convert-to", target,
            "--outdir", out_dir,
        ]
        if filter_key in LO_FILTERS:
            cmd += ["--infilter=" + LO_FILTERS[filter_key]]
        cmd.append(src_path)

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                               env={**os.environ, "HOME": tmpdir})
        
        out_files = [f for f in os.listdir(out_dir) if f.endswith(f".{target}")]
        if not out_files:
            return f"conversion failed: {result.stderr[:500]}", 500

        out_path = os.path.join(out_dir, out_files[0])
        return send_file(out_path, as_attachment=True,
                        download_name=file.filename.rsplit(".", 1)[0] + f".{target}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
