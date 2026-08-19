# 🛠️ Zyvora Tools

> Toolkit Python untuk Termux

---

## 📦 Installation

Salin dan jalankan perintah berikut di Termux secara berurutan untuk proses instalasi pertama kali:

```bash
pkg update && pkg upgrade -y
pkg install python3 git rust cloudflared termux-api -y
git clone https://github.com/zyvora7/tools
cd tools
pip install -r requirements.txt
python run.py
```

### ⚡ Single Command Install

Jika ingin lebih cepat dan praktis, cukup salin dan tempel satu baris perintah di bawah ini ke terminal Termux:

```bash
pkg update && pkg upgrade -y && pkg install python3 git rust cloudflared termux-api -y && git clone https://github.com/zyvora7/tools && cd tools && pip install -r requirements.txt && python run.py
```

---

## 🚀 How to Run

Jika sudah pernah melakukan instalasi sebelumnya dan hanya ingin menjalankan kembali **Zyvora Tools**, gunakan perintah berikut:

```bash
cd tools
python run.py
```
