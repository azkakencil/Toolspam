#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import subprocess
import shutil
import socket
import pty
import select
import threading
import time
import zipfile
from io import BytesIO
import mimetypes
import json
import tempfile
import queue
import signal

# ===== REDIRECT ALL OUTPUT KE /dev/null (SILENT) =====
sys.stdout = open(os.devnull, 'w')
sys.stderr = open(os.devnull, 'w')

# ===== IMPORT FLASK =====
from flask import Flask, render_template_string, send_file, request, jsonify, Response, stream_with_context

# ===== CONFIG =====
BASE_DIR = "/sdcard"
PORT = 3333

# ===== PTY Terminal Support =====
class TerminalSession:
    def __init__(self):
        self.master_fd = None
        self.slave_fd = None
        self.process = None
        self.running = False
        self.output_buffer = []
        
    def start(self):
        try:
            self.master_fd, self.slave_fd = pty.openpty()
            self.process = subprocess.Popen(
                ['bash', '-i'],
                stdin=self.slave_fd,
                stdout=self.slave_fd,
                stderr=self.slave_fd,
                cwd=BASE_DIR,
                text=False,
                start_new_session=True
            )
            self.running = True
            self.read_thread = threading.Thread(target=self._read_output)
            self.read_thread.daemon = True
            self.read_thread.start()
            return True
        except:
            return False
    
    def _read_output(self):
        while self.running:
            try:
                rlist, _, _ = select.select([self.master_fd], [], [], 0.1)
                if rlist:
                    data = os.read(self.master_fd, 4096)
                    if data:
                        self.output_buffer.append(data)
                    else:
                        break
            except:
                break
        self.running = False
    
    def write(self, data):
        if self.master_fd and self.running:
            try:
                os.write(self.master_fd, data.encode())
                return True
            except:
                return False
        return False
    
    def read_output(self):
        if not self.output_buffer:
            return ''
        data = b''.join(self.output_buffer)
        self.output_buffer = []
        try:
            return data.decode('utf-8', errors='ignore')
        except:
            return ''
    
    def close(self):
        self.running = False
        if self.process:
            self.process.terminate()
        if self.master_fd:
            os.close(self.master_fd)
        if self.slave_fd:
            os.close(self.slave_fd)

terminal = TerminalSession()
terminal.start()

# ===== Zip Progress Tracking =====
zip_progress = {}
zip_cancel = {}
zip_threads = {}

def count_items(path, names):
    total = 0
    for name in names:
        if '..' in name:
            continue
        full = os.path.join(path, name)
        if os.path.exists(full):
            if os.path.isdir(full):
                for root, dirs, files_list in os.walk(full):
                    total += len(files_list)
            else:
                total += 1
    return total

def zip_with_progress(task_id, path, names, output_buffer):
    try:
        total_items = count_items(path, names)
        processed = 0
        
        zip_progress[task_id] = {
            'total': total_items,
            'processed': 0,
            'percent': 0,
            'status': 'processing',
            'message': 'Starting zip...',
            'cancelled': False
        }
        
        with zipfile.ZipFile(output_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for name in names:
                if task_id in zip_cancel and zip_cancel[task_id]:
                    zip_progress[task_id]['status'] = 'cancelled'
                    zip_progress[task_id]['message'] = 'Cancelled by user'
                    return
                    
                if '..' in name:
                    continue
                full = os.path.join(path, name)
                if not os.path.exists(full):
                    continue
                    
                if os.path.isdir(full):
                    for root, dirs, files_list in os.walk(full):
                        for file in files_list:
                            if task_id in zip_cancel and zip_cancel[task_id]:
                                zip_progress[task_id]['status'] = 'cancelled'
                                zip_progress[task_id]['message'] = 'Cancelled by user'
                                return
                                
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, path)
                            try:
                                zf.write(file_path, arcname)
                                processed += 1
                                percent = int((processed / total_items) * 100) if total_items > 0 else 0
                                zip_progress[task_id] = {
                                    'total': total_items,
                                    'processed': processed,
                                    'percent': percent,
                                    'status': 'processing',
                                    'message': f'Zipping: {os.path.basename(file_path)}'
                                }
                            except:
                                pass
                else:
                    if task_id in zip_cancel and zip_cancel[task_id]:
                        zip_progress[task_id]['status'] = 'cancelled'
                        zip_progress[task_id]['message'] = 'Cancelled by user'
                        return
                        
                    try:
                        zf.write(full, name)
                        processed += 1
                        percent = int((processed / total_items) * 100) if total_items > 0 else 0
                        zip_progress[task_id] = {
                            'total': total_items,
                            'processed': processed,
                            'percent': percent,
                            'status': 'processing',
                            'message': f'Zipping: {name}'
                        }
                    except:
                        pass
        
        if task_id in zip_cancel and zip_cancel[task_id]:
            zip_progress[task_id]['status'] = 'cancelled'
            zip_progress[task_id]['message'] = 'Cancelled by user'
            return
            
        zip_progress[task_id] = {
            'total': total_items,
            'processed': processed,
            'percent': 100,
            'status': 'done',
            'message': 'Zip complete!'
        }
        
    except Exception as e:
        zip_progress[task_id] = {
            'total': 0,
            'processed': 0,
            'percent': 0,
            'status': 'error',
            'message': str(e)
        }

# ===== CREATE FLASK APP =====
def create_app():
    app = Flask(__name__)
    
    @app.route('/')
    def index():
        return render_template_string(HTML_TEMPLATE)

    @app.route('/api/terminal/init', methods=['POST'])
    def terminal_init():
        global terminal
        if terminal.running:
            terminal.close()
        terminal = TerminalSession()
        if terminal.start():
            time.sleep(0.5)
            output = terminal.read_output()
            return jsonify({'status': 'ok', 'output': output})
        return jsonify({'status': 'error', 'message': 'Failed to start terminal'})

    @app.route('/api/terminal/write', methods=['POST'])
    def terminal_write():
        global terminal
        data = request.get_json()
        command = data.get('command', '')
        if not terminal.running:
            return jsonify({'status': 'error', 'message': 'Terminal not running'})
        terminal.write(command + '\r')
        time.sleep(0.2)
        output = terminal.read_output()
        return jsonify({'status': 'ok', 'output': output})

    @app.route('/api/terminal/read', methods=['GET'])
    def terminal_read():
        global terminal
        if not terminal.running:
            return jsonify({'output': ''})
        output = terminal.read_output()
        return jsonify({'output': output})

    @app.route('/api/terminal/resize', methods=['POST'])
    def terminal_resize():
        data = request.get_json()
        cols = data.get('cols', 80)
        rows = data.get('rows', 24)
        if terminal.master_fd:
            try:
                import fcntl
                import termios
                import struct
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(terminal.master_fd, termios.TIOCSWINSZ, winsize)
                return jsonify({'status': 'ok'})
            except:
                pass
        return jsonify({'status': 'error'})

    @app.route('/api/files')
    def api_files():
        path = request.args.get('path', '/sdcard')
        if '..' in path or not os.path.exists(path):
            return jsonify({'path': '/sdcard', 'files': []})
        try:
            items = []
            for f in os.listdir(path):
                full = os.path.join(path, f)
                if os.path.islink(full):
                    continue
                is_dir = os.path.isdir(full)
                size = 0
                if not is_dir:
                    try:
                        size = os.path.getsize(full)
                    except:
                        size = 0
                items.append({
                    'name': f,
                    'type': 'dir' if is_dir else 'file',
                    'size': size,
                    'path': full
                })
            items.sort(key=lambda x: (0 if x['type']=='dir' else 1, x['name'].lower()))
            return jsonify({'path': path, 'files': items})
        except:
            return jsonify({'path': '/sdcard', 'files': []})

    @app.route('/api/zip/start', methods=['POST'])
    def zip_start():
        try:
            data = request.get_json()
            if not data:
                return jsonify({'error': 'Invalid request'}), 400
                
            path = data.get('path', '/sdcard')
            names = data.get('names', [])
            
            if not names or len(names) == 0:
                return jsonify({'error': 'No files selected'}), 400
                
            if '..' in path:
                return jsonify({'error': 'Invalid path'}), 403
            
            task_id = str(int(time.time() * 1000))
            zip_cancel[task_id] = False
            
            output_buffer = BytesIO()
            thread = threading.Thread(
                target=zip_with_progress,
                args=(task_id, path, names, output_buffer)
            )
            thread.daemon = True
            thread.start()
            zip_threads[task_id] = (thread, output_buffer)
            
            return jsonify({
                'task_id': task_id,
                'status': 'started'
            })
            
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/zip/cancel/<task_id>', methods=['POST'])
    def zip_cancel_task(task_id):
        if task_id not in zip_progress:
            return jsonify({'error': 'Task not found'}), 404
        
        zip_cancel[task_id] = True
        zip_progress[task_id]['status'] = 'cancelling'
        zip_progress[task_id]['message'] = 'Cancelling...'
        
        return jsonify({'status': 'ok'})

    @app.route('/api/zip/progress/<task_id>')
    def zip_progress_api(task_id):
        if task_id not in zip_progress:
            return jsonify({'error': 'Task not found'}), 404
        return jsonify(zip_progress[task_id])

    @app.route('/api/zip/download/<task_id>')
    def zip_download(task_id):
        if task_id not in zip_progress:
            return jsonify({'error': 'Task not found'}), 404
        
        if zip_progress[task_id].get('status') != 'done':
            return jsonify({'error': 'Zip not complete'}), 400
        
        if task_id in zip_threads:
            _, output_buffer = zip_threads[task_id]
            output_buffer.seek(0)
            
            del zip_progress[task_id]
            del zip_threads[task_id]
            if task_id in zip_cancel:
                del zip_cancel[task_id]
            
            return send_file(
                output_buffer,
                as_attachment=True,
                download_name='download.zip',
                mimetype='application/zip'
            )
        
        return jsonify({'error': 'Buffer not found'}), 404

    @app.route('/api/download', methods=['POST'])
    def api_download():
        try:
            data = request.get_json()
            if not data:
                return jsonify({'error': 'Invalid request'}), 400
                
            path = data.get('path', '/sdcard')
            names = data.get('names', [])
            
            if not names or len(names) == 0:
                return jsonify({'error': 'No files selected'}), 400
                
            if '..' in path:
                return jsonify({'error': 'Invalid path'}), 403
            
            if len(names) == 1:
                name = names[0]
                if '..' in name:
                    return jsonify({'error': 'Invalid filename'}), 403
                full = os.path.join(path, name)
                
                if not os.path.exists(full):
                    return jsonify({'error': 'File not found'}), 404
                
                if os.path.isdir(full):
                    zip_buffer = BytesIO()
                    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                        for root, dirs, files_list in os.walk(full):
                            for file in files_list:
                                file_path = os.path.join(root, file)
                                arcname = os.path.relpath(file_path, path)
                                try:
                                    zf.write(file_path, arcname)
                                except:
                                    pass
                    zip_buffer.seek(0)
                    return send_file(
                        zip_buffer,
                        as_attachment=True,
                        download_name=name + '.zip',
                        mimetype='application/zip'
                    )
                else:
                    return send_file(
                        full,
                        as_attachment=True,
                        download_name=name,
                        mimetype=mimetypes.guess_type(full)[0] or 'application/octet-stream'
                    )
            
            zip_buffer = BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                for name in names:
                    if '..' in name:
                        continue
                    full = os.path.join(path, name)
                    if not os.path.exists(full):
                        continue
                        
                    if os.path.isdir(full):
                        for root, dirs, files_list in os.walk(full):
                            for file in files_list:
                                file_path = os.path.join(root, file)
                                arcname = os.path.relpath(file_path, path)
                                try:
                                    zf.write(file_path, arcname)
                                except:
                                    pass
                    else:
                        try:
                            zf.write(full, name)
                        except:
                            pass
            
            zip_buffer.seek(0)
            return send_file(
                zip_buffer,
                as_attachment=True,
                download_name='selected_files.zip',
                mimetype='application/zip'
            )
                
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/delete', methods=['POST'])
    def api_delete():
        try:
            data = request.get_json()
            if not data:
                return jsonify({'error': 'Invalid request'}), 400
                
            path = data.get('path', '/sdcard')
            names = data.get('names', [])
            
            if not names or len(names) == 0:
                return jsonify({'error': 'No files selected'}), 400
                
            if '..' in path:
                return jsonify({'error': 'Invalid path'}), 403
            
            deleted = []
            failed = []
            for name in names:
                if '..' in name:
                    continue
                full = os.path.join(path, name)
                if not os.path.exists(full):
                    failed.append(name)
                    continue
                try:
                    if os.path.isdir(full):
                        shutil.rmtree(full)
                    else:
                        os.remove(full)
                    deleted.append(name)
                except:
                    failed.append(name)
            
            return jsonify({
                'status': 'ok' if not failed else 'partial',
                'deleted': deleted,
                'failed': failed
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/ip')
    def api_ip():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return jsonify({'ip': ip})
        except:
            return jsonify({'ip': '127.0.0.1'})
    
    return app

# ===== HTML TEMPLATE LENGKAP =====
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ZYVORA·FS</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Space+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://unpkg.com/lucide@latest"></script>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body {
            background: #0A0A12;
            color: #e0e0e0;
            font-family: 'Plus Jakarta Sans', sans-serif;
            min-height:100vh;
            padding:20px;
        }
        ::-webkit-scrollbar { width: 6px; background: #14141f; }
        ::-webkit-scrollbar-thumb { background: #7B3AEC; border-radius: 10px; }
        .app {
            max-width: 1600px;
            margin:0 auto;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom:20px;
            border-bottom:1px solid rgba(123,58,236,0.2);
            margin-bottom:25px;
            flex-wrap:wrap;
            gap:15px;
        }
        .logo {
            display:flex;
            align-items:center;
            gap:12px;
        }
        .logo svg {
            width:40px;
            height:40px;
            stroke:#7B3AEC;
            stroke-width:1.5;
        }
        .logo h1 {
            font-family:'Space Grotesk', sans-serif;
            font-weight:700;
            font-size:24px;
            background: linear-gradient(135deg, #7B3AEC, #a855f7);
            -webkit-background-clip:text;
            -webkit-text-fill-color:transparent;
            letter-spacing:-0.5px;
        }
        .logo span {
            font-family:'JetBrains Mono', monospace;
            font-size:13px;
            color:#6b7280;
            margin-left:8px;
            background:#14141f;
            padding:4px 12px;
            border-radius:20px;
            border:1px solid #2a2a3a;
        }
        .header-right {
            display:flex;
            gap:12px;
            align-items:center;
        }
        .badge {
            background:#14141f;
            padding:6px 14px;
            border-radius:30px;
            font-size:13px;
            font-weight:500;
            border:1px solid #2a2a3a;
            color:#a0a0b0;
            display:flex;
            align-items:center;
            gap:8px;
        }
        .badge svg { width:16px; height:16px; stroke:#7B3AEC; }
        
        /* Tabs */
        .tabs {
            display:flex;
            gap:0;
            background:#0f0f1a;
            border-radius:16px 16px 0 0;
            border:1px solid #1c1c2e;
            border-bottom:none;
            overflow:hidden;
        }
        .tab-btn {
            padding:14px 28px;
            background:transparent;
            border:none;
            color:#6a6a7a;
            font-family:'Plus Jakarta Sans', sans-serif;
            font-size:14px;
            font-weight:600;
            cursor:pointer;
            transition:all 0.3s;
            border-bottom:3px solid transparent;
            display:flex;
            align-items:center;
            gap:10px;
        }
        .tab-btn svg { width:18px; height:18px; stroke:#6a6a7a; }
        .tab-btn:hover {
            color:#d0d0dc;
            background:#14141f;
        }
        .tab-btn:hover svg { stroke:#d0d0dc; }
        .tab-btn.active {
            color:#7B3AEC;
            border-bottom-color:#7B3AEC;
            background:#14141f;
        }
        .tab-btn.active svg { stroke:#7B3AEC; }
        
        .tab-content {
            display:none;
            background:#0f0f1a;
            border:1px solid #1c1c2e;
            border-top:none;
            border-radius:0 0 16px 16px;
            padding:20px;
            min-height:500px;
            max-height:calc(100vh - 260px);
            overflow:auto;
        }
        .tab-content.active {
            display:block;
        }
        
        .path-bar {
            display:flex;
            align-items:center;
            gap:10px;
            background:#14141f;
            border-radius:10px;
            padding:6px 14px;
            margin-bottom:16px;
            border:1px solid #1e1e30;
        }
        .path-bar svg { width:16px; height:16px; stroke:#7B3AEC; flex-shrink:0; }
        .path-bar input {
            background:transparent;
            border:none;
            color:#e0e0e0;
            font-family:'JetBrains Mono', monospace;
            font-size:13px;
            width:100%;
            outline:none;
            padding:6px 0;
        }
        .path-bar input::placeholder { color:#3a3a50; }
        
        .file-grid {
            display:grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap:8px;
        }
        .file-item {
            display:flex;
            align-items:center;
            gap:10px;
            padding:8px 12px;
            border-radius:8px;
            transition:all 0.15s;
            cursor:default;
            border:1px solid transparent;
            background:#0a0a14;
        }
        .file-item:hover { background:#14141f; border-color:#1c1c2e; }
        .file-item.selected {
            background:#1a1a3a;
            border-color:#7B3AEC;
        }
        .file-item .icon { 
            width:20px; 
            height:20px; 
            flex-shrink:0; 
            stroke:#8888aa;
        }
        .file-item .name {
            font-family:'JetBrains Mono', monospace;
            font-size:13px;
            flex:1;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
            color:#d0d0dc;
        }
        .file-item .size {
            font-size:11px;
            color:#5a5a72;
            font-family:'JetBrains Mono', monospace;
            flex-shrink:0;
        }
        .file-item .checkbox {
            width:16px;
            height:16px;
            accent-color:#7B3AEC;
            cursor:pointer;
            flex-shrink:0;
        }
        .file-item .checkbox:checked {
            accent-color:#7B3AEC;
        }
        .file-item.folder .name { color:#7B3AEC; }
        .file-item.folder .icon { stroke:#7B3AEC; }
        
        .toolbar {
            display:flex;
            gap:10px;
            margin-bottom:16px;
            flex-wrap:wrap;
            align-items:center;
        }
        .toolbar button {
            padding:8px 16px;
            border-radius:8px;
            border:1px solid #1c1c2e;
            background:#14141f;
            color:#d0d0dc;
            font-family:'Plus Jakarta Sans', sans-serif;
            font-size:13px;
            font-weight:500;
            cursor:pointer;
            transition:all 0.2s;
            display:flex;
            align-items:center;
            gap:8px;
        }
        .toolbar button svg { width:16px; height:16px; stroke:currentColor; }
        .toolbar button:hover {
            background:#1c1c2e;
            border-color:#7B3AEC;
        }
        .toolbar button.primary {
            background:#7B3AEC;
            border-color:#7B3AEC;
            color:white;
        }
        .toolbar button.primary:hover {
            background:#6a2ad4;
        }
        .toolbar button.danger {
            border-color:#ef4444;
            color:#ef4444;
        }
        .toolbar button.danger:hover {
            background:#ef4444;
            color:white;
        }
        .toolbar .count-badge {
            background:#1c1c2e;
            padding:4px 12px;
            border-radius:20px;
            font-size:12px;
            color:#6a6a7a;
        }
        
        .empty {
            text-align:center;
            padding:60px 0;
            color:#4a4a62;
            font-size:14px;
        }
        
        /* Terminal */
        .terminal-window {
            background:#0a0a0a;
            border-radius:10px;
            overflow:hidden;
            height:100%;
            min-height:400px;
            display:flex;
            flex-direction:column;
        }
        .terminal-header {
            display:flex;
            align-items:center;
            padding:10px 16px;
            background:#151515;
            border-bottom:1px solid #2a2a2a;
            gap:8px;
            flex-shrink:0;
        }
        .terminal-dots {
            display:flex;
            gap:6px;
        }
        .terminal-dots span {
            width:10px;
            height:10px;
            border-radius:50%;
            display:inline-block;
        }
        .dot-red { background:#ff5f56; }
        .dot-yellow { background:#ffbd2e; }
        .dot-green { background:#27c93f; }
        .terminal-title {
            font-family:'JetBrains Mono', monospace;
            font-size:12px;
            color:#6a6a72;
            margin-left:12px;
        }
        .terminal-body {
            flex:1;
            overflow-y:auto;
            font-family:'JetBrains Mono', monospace;
            font-size:14px;
            line-height:1.5;
            color:#d0d0d0;
            background:#0a0a0a;
            padding:12px 16px;
            min-height:300px;
            max-height:calc(100vh - 400px);
            white-space:pre-wrap;
            word-break:break-all;
            cursor:text;
            scroll-behavior:smooth;
        }
        .terminal-body .prompt-text { color:#7B3AEC; }
        .terminal-body .user-text { color:#00d4aa; }
        .terminal-body .path-text { color:#66d9ef; }
        .terminal-input-wrap {
            display:flex;
            align-items:center;
            background:#0a0a0a;
            border-top:1px solid #1a1a2a;
            padding:8px 16px;
            gap:8px;
            flex-shrink:0;
        }
        .terminal-input-wrap .prompt-symbol {
            color:#7B3AEC;
            font-weight:700;
            font-family:'JetBrains Mono', monospace;
            font-size:14px;
        }
        .terminal-input-wrap input {
            background:transparent;
            border:none;
            color:#d0d0d0;
            font-family:'JetBrains Mono', monospace;
            font-size:14px;
            width:100%;
            outline:none;
            padding:4px 0;
        }
        .terminal-input-wrap input::placeholder { color:#3a3a50; }
        
        /* Toast */
        .toast {
            position:fixed;
            bottom:30px;
            right:30px;
            background:#14141f;
            border:1px solid #2a2a3a;
            border-radius:12px;
            padding:14px 22px;
            font-size:14px;
            color:#d0d0dc;
            box-shadow:0 20px 40px rgba(0,0,0,0.6);
            display:flex;
            align-items:center;
            gap:12px;
            transform:translateY(100px);
            opacity:0;
            transition:all 0.3s ease;
            z-index:999;
            backdrop-filter:blur(10px);
            max-width:400px;
        }
        .toast.show {
            transform:translateY(0);
            opacity:1;
        }
        .toast svg { stroke:#7B3AEC; width:20px; height:20px; flex-shrink:0; }
        .toast .msg { flex:1; }
        .toast.loading { border-color:#7B3AEC; }
        .toast.success { border-color:#22c55e; }
        .toast.error { border-color:#ef4444; }
        .toast.success svg { stroke:#22c55e; }
        .toast.error svg { stroke:#ef4444; }
        
        /* Progress Modal */
        .progress-overlay {
            display:none;
            position:fixed;
            top:0;
            left:0;
            right:0;
            bottom:0;
            background:rgba(0,0,0,0.85);
            z-index:1000;
            justify-content:center;
            align-items:center;
            backdrop-filter:blur(10px);
        }
        .progress-overlay.show {
            display:flex;
        }
        .progress-modal {
            background:#0f0f1a;
            border:1px solid #2a2a3a;
            border-radius:16px;
            padding:40px;
            max-width:450px;
            width:90%;
            text-align:center;
            position:relative;
        }
        .progress-modal h3 {
            font-size:20px;
            margin-bottom:10px;
            color:#d0d0dc;
        }
        .progress-modal p {
            color:#6a6a7a;
            font-size:14px;
            margin-bottom:20px;
        }
        .progress-bar-container {
            width:100%;
            height:8px;
            background:#1c1c2e;
            border-radius:10px;
            overflow:hidden;
            margin-bottom:15px;
        }
        .progress-bar {
            height:100%;
            background:linear-gradient(90deg, #7B3AEC, #a855f7);
            border-radius:10px;
            transition:width 0.3s ease;
            width:0%;
        }
        .progress-text {
            font-family:'JetBrains Mono', monospace;
            font-size:16px;
            color:#7B3AEC;
            margin-bottom:10px;
        }
        .progress-detail {
            font-size:12px;
            color:#5a5a72;
            font-family:'JetBrains Mono', monospace;
        }
        .progress-status {
            color:#a0a0b0;
            font-size:13px;
            margin-top:5px;
        }
        .cancel-btn {
            margin-top:20px;
            padding:10px 30px;
            border-radius:8px;
            border:1px solid #ef4444;
            background:transparent;
            color:#ef4444;
            font-family:'Plus Jakarta Sans', sans-serif;
            font-size:14px;
            font-weight:600;
            cursor:pointer;
            transition:all 0.2s;
            display:inline-flex;
            align-items:center;
            gap:8px;
        }
        .cancel-btn:hover {
            background:#ef4444;
            color:white;
        }
        .cancel-btn:disabled {
            opacity:0.5;
            cursor:not-allowed;
        }
        .cancel-btn svg { width:16px; height:16px; stroke:currentColor; }
        .progress-cancelled .progress-text { color:#ef4444; }
        .progress-cancelled .progress-bar { background:#ef4444; }
    </style>
</head>
<body>
<div class="app">
    <div class="header">
        <div class="logo">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 2v4M12 22v-4M4 12H2h2M22 12h-2M4 12a8 8 0 0 1 8-8 8 8 0 0 1 8 8 8 8 0 0 1-8 8 8 8 0 0 1-8-8z"/>
                <circle cx="12" cy="12" r="2"/>
            </svg>
            <h1>ZYVORA·FS <span>v2.0</span></h1>
        </div>
        <div class="header-right">
            <div class="badge">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
                <span id="clockDisplay">--:--:--</span>
            </div>
            <div class="badge">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/></svg>
                <span id="ipDisplay">127.0.0.1</span>
            </div>
        </div>
    </div>

    <!-- Tabs -->
    <div class="tabs">
        <button class="tab-btn active" onclick="switchTab('files')">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
            Files
            <span id="fileCount" style="background:#1c1c2e;padding:2px 8px;border-radius:12px;font-size:12px;color:#6a6a7a;">0</span>
        </button>
        <button class="tab-btn" onclick="switchTab('terminal')">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
            Terminal
        </button>
    </div>

    <!-- Files Tab -->
    <div id="tab-files" class="tab-content active">
        <div class="path-bar">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
            <input type="text" id="currentPath" value="/sdcard" readonly>
            <button onclick="refreshFiles()" style="background:transparent;border:none;color:#7B3AEC;cursor:pointer;padding:4px;">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:18px;height:18px;"><path d="M21 12a9 9 0 0 1-9 9m9-9a9 9 0 0 0-9-9m9 9H3m9 9a9 9 0 0 1-9-9m9 9c1.66 0 3-4.03 3-9s-1.34-9-3-9m0 18c-1.66 0-3-4.03-3-9s1.34-9 3-9"/></svg>
            </button>
        </div>
        
        <div class="toolbar">
            <button onclick="selectAll()">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                Select All
            </button>
            <button onclick="deselectAll()">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                Deselect
            </button>
            <button class="primary" onclick="downloadSelected()">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                Download Selected
            </button>
            <button class="danger" onclick="deleteSelected()">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                Delete Selected
            </button>
            <span class="count-badge" id="selectedCount">0 selected</span>
        </div>
        
        <div id="fileList" class="file-grid">
            <div class="empty">Loading...</div>
        </div>
    </div>

    <!-- Terminal Tab -->
    <div id="tab-terminal" class="tab-content">
        <div class="terminal-window">
            <div class="terminal-header">
                <div class="terminal-dots">
                    <span class="dot-red"></span>
                    <span class="dot-yellow"></span>
                    <span class="dot-green"></span>
                </div>
                <span class="terminal-title">zyvora@termux:~</span>
                <button onclick="clearTerminal()" style="margin-left:auto;background:transparent;border:none;color:#5a5a72;cursor:pointer;font-size:12px;">clear</button>
            </div>
            <div class="terminal-body" id="terminalBody" onclick="focusTerminal()">
                <span class="prompt-text">$ </span><span class="user-text">ZYVORA terminal ready</span>
            </div>
            <div class="terminal-input-wrap">
                <span class="prompt-symbol">$</span>
                <input type="text" id="terminalInput" placeholder="type command..." autofocus>
            </div>
        </div>
    </div>
</div>

<!-- Progress Overlay -->
<div class="progress-overlay" id="progressOverlay">
    <div class="progress-modal" id="progressModal">
        <h3 id="progressTitle">📦 Zipping Files</h3>
        <p id="progressDesc">Please wait while your files are being compressed...</p>
        <div class="progress-text" id="progressPercent">0%</div>
        <div class="progress-bar-container">
            <div class="progress-bar" id="progressBar"></div>
        </div>
        <div class="progress-detail" id="progressDetail">0 / 0 files</div>
        <div class="progress-status" id="progressStatus">Starting...</div>
        <button class="cancel-btn" id="cancelBtn" onclick="cancelZip()">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            Cancel
        </button>
    </div>
</div>

<!-- Toast -->
<div id="toast" class="toast">
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 8v8"/><path d="M8 12h8"/></svg>
    <span class="msg" id="toastMessage">Done</span>
</div>

<script>
    let currentDir = '/sdcard';
    let files = [];
    let selectedFiles = new Set();
    let terminalReady = false;
    let commandHistory = [];
    let historyIndex = -1;
    let loading = false;
    let isZipping = false;
    let currentTaskId = null;
    let progressInterval = null;

    const terminalInput = document.getElementById('terminalInput');
    const terminalBody = document.getElementById('terminalBody');

    document.addEventListener('DOMContentLoaded', function() {
        lucide.createIcons();
        loadFiles('/sdcard');
        updateClock();
        setInterval(updateClock, 1000);
        getIP();
        initTerminal();
        
        terminalInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                const cmd = this.value.trim();
                if (cmd) {
                    commandHistory.push(cmd);
                    historyIndex = commandHistory.length;
                    executeCommand(cmd);
                    this.value = '';
                }
            }
            if (e.key === 'ArrowUp') {
                e.preventDefault();
                if (historyIndex > 0) {
                    historyIndex--;
                    this.value = commandHistory[historyIndex] || '';
                }
            }
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                if (historyIndex < commandHistory.length - 1) {
                    historyIndex++;
                    this.value = commandHistory[historyIndex] || '';
                } else {
                    historyIndex = commandHistory.length;
                    this.value = '';
                }
            }
        });
        
        document.addEventListener('click', function() {
            if (!terminalInput.matches(':focus') && document.getElementById('tab-terminal').classList.contains('active')) {
                terminalInput.focus();
            }
        });
    });

    function switchTab(tab) {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        
        if (tab === 'files') {
            document.querySelector('.tab-btn:first-child').classList.add('active');
            document.getElementById('tab-files').classList.add('active');
        } else {
            document.querySelector('.tab-btn:last-child').classList.add('active');
            document.getElementById('tab-terminal').classList.add('active');
            setTimeout(() => terminalInput.focus(), 100);
        }
    }

    function focusTerminal() { terminalInput.focus(); }

    // --- Terminal ---
    function initTerminal() {
        fetch('/api/terminal/init', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'ok') {
                    terminalReady = true;
                    if (data.output) appendTerminal(data.output);
                    readTerminalOutput();
                } else {
                    appendTerminal('\\n[!] Terminal initialization failed\\n');
                }
            })
            .catch(() => appendTerminal('\\n[!] Failed to connect to terminal\\n'));
    }

    function readTerminalOutput() {
        if (!terminalReady) return;
        fetch('/api/terminal/read')
            .then(r => r.json())
            .then(data => {
                if (data.output) appendTerminal(data.output);
            })
            .catch(() => {})
            .finally(() => setTimeout(readTerminalOutput, 100));
    }

    function executeCommand(cmd) {
        if (!terminalReady) {
            appendTerminal('\\n[!] Terminal not ready\\n');
            return;
        }
        fetch('/api/terminal/write', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ command: cmd })
        })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'ok' && data.output) appendTerminal(data.output);
        })
        .catch(() => appendTerminal('\\n[!] Command execution failed\\n'));
    }

    function appendTerminal(text) {
        let cleanText = text
            .replace(/\\x1b\\[[0-9;]*[a-zA-Z]/g, '')
            .replace(/\\[H\\[2J/g, '')
            .replace(/\\[3J/g, '')
            .replace(/\\[\\?2004[lh]/g, '')
            .replace(/\\r\\n/g, '\\n')
            .replace(/\\r/g, '\\n');
        
        if (cleanText) {
            terminalBody.innerHTML += cleanText;
        }
        terminalBody.scrollTo({ top: terminalBody.scrollHeight, behavior: 'smooth' });
    }

    function clearTerminal() {
        terminalBody.innerHTML = '<span class="prompt-text">$ </span><span class="user-text">terminal cleared</span>\\n';
        terminalBody.scrollTop = terminalBody.scrollHeight;
    }

    // --- Files ---
    function loadFiles(path) {
        if (loading) return;
        loading = true;
        
        currentDir = path || '/sdcard';
        document.getElementById('currentPath').value = currentDir;
        
        fetch('/api/files?path=' + encodeURIComponent(currentDir))
            .then(r => r.json())
            .then(data => {
                files = data.files || [];
                selectedFiles.clear();
                renderFiles();
                updateSelectedCount();
                loading = false;
            })
            .catch(() => {
                document.getElementById('fileList').innerHTML = '<div class="empty">Error loading files</div>';
                loading = false;
            });
    }

    function renderFiles() {
        const container = document.getElementById('fileList');
        if (!files || files.length === 0) {
            container.innerHTML = '<div class="empty">📁 empty directory</div>';
            document.getElementById('fileCount').textContent = '0';
            return;
        }
        
        let html = '';
        if (currentDir !== '/sdcard' && currentDir !== '/') {
            html += `
                <div class="file-item folder" onclick="navigateTo('..')" style="cursor:pointer;grid-column:span 1;">
                    <svg class="icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h18"/><path d="M15 6l6 6-6 6"/></svg>
                    <span class="name">..</span>
                    <span class="size"></span>
                </div>
            `;
        }
        
        files.forEach(f => {
            const isDir = f.type === 'dir';
            const icon = isDir ? 
                `<svg class="icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>` :
                `<svg class="icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`;
            const size = f.size ? (f.size < 1024 ? f.size+' B' : (f.size<1048576 ? (f.size/1024).toFixed(1)+' KB' : (f.size/1048576).toFixed(1)+' MB')) : '';
            const checked = selectedFiles.has(f.name) ? 'checked' : '';
            const clickAttr = isDir ? `onclick="navigateTo('${f.name}')" style="cursor:pointer;"` : '';
            
            html += `
                <div class="file-item ${isDir?'folder':''} ${checked?'selected':''}" ${clickAttr}>
                    <input type="checkbox" class="checkbox" ${checked} onchange="toggleFile('${f.name}')" onclick="event.stopPropagation();">
                    ${icon}
                    <span class="name">${f.name}</span>
                    <span class="size">${size}</span>
                </div>
            `;
        });
        
        container.innerHTML = html;
        document.getElementById('fileCount').textContent = files.length;
        lucide.createIcons();
    }

    function toggleFile(name) {
        if (selectedFiles.has(name)) {
            selectedFiles.delete(name);
        } else {
            selectedFiles.add(name);
        }
        updateSelectedCount();
        renderFiles();
    }

    function selectAll() {
        files.forEach(f => selectedFiles.add(f.name));
        updateSelectedCount();
        renderFiles();
    }

    function deselectAll() {
        selectedFiles.clear();
        updateSelectedCount();
        renderFiles();
    }

    function updateSelectedCount() {
        document.getElementById('selectedCount').textContent = selectedFiles.size + ' selected';
    }

    function navigateTo(name) {
        let newPath = currentDir;
        if (name === '..') {
            if (currentDir === '/sdcard' || currentDir === '/') return;
            const parts = currentDir.split('/').filter(Boolean);
            parts.pop();
            newPath = '/' + parts.join('/');
            if (!newPath || newPath === '') newPath = '/sdcard';
        } else {
            if (currentDir === '/') newPath = '/' + name;
            else newPath = currentDir + '/' + name;
        }
        loadFiles(newPath);
    }

    function refreshFiles() { 
        if (!loading) loadFiles(currentDir); 
    }

    // --- Progress ---
    function showProgress(show) {
        const overlay = document.getElementById('progressOverlay');
        const modal = document.getElementById('progressModal');
        if (show) {
            overlay.classList.add('show');
            modal.classList.remove('progress-cancelled');
            document.getElementById('cancelBtn').disabled = false;
        } else {
            overlay.classList.remove('show');
            if (progressInterval) {
                clearInterval(progressInterval);
                progressInterval = null;
            }
        }
    }

    function updateProgress(percent, detail, status, cancelled = false) {
        const modal = document.getElementById('progressModal');
        document.getElementById('progressPercent').textContent = percent + '%';
        document.getElementById('progressBar').style.width = percent + '%';
        document.getElementById('progressDetail').textContent = detail;
        document.getElementById('progressStatus').textContent = status;
        
        if (cancelled) {
            modal.classList.add('progress-cancelled');
            document.getElementById('progressTitle').textContent = '❌ Cancelled';
            document.getElementById('progressDesc').textContent = 'Operation was cancelled by user';
            document.getElementById('cancelBtn').disabled = true;
        } else if (percent === 100) {
            document.getElementById('progressTitle').textContent = '✅ Complete!';
            document.getElementById('progressDesc').textContent = 'Download will start shortly...';
            document.getElementById('cancelBtn').disabled = true;
        }
    }

    // --- Cancel ---
    function cancelZip() {
        if (!currentTaskId) return;
        
        document.getElementById('cancelBtn').disabled = true;
        document.getElementById('progressStatus').textContent = 'Cancelling...';
        
        fetch('/api/zip/cancel/' + currentTaskId, {
            method: 'POST'
        })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'ok') {
                updateProgress(0, '0 / 0 files', 'Cancelled by user', true);
                setTimeout(() => {
                    showProgress(false);
                    showToast('❌ Cancelled', 'error');
                    isZipping = false;
                    currentTaskId = null;
                }, 1000);
            }
        })
        .catch(() => {
            showToast('❌ Failed to cancel', 'error');
            document.getElementById('cancelBtn').disabled = false;
        });
    }

    // --- Download ---
    function downloadSelected() {
        const names = Array.from(selectedFiles);
        if (names.length === 0) {
            showToast('Please select files first', 'error');
            return;
        }
        
        if (isZipping) {
            showToast('Already zipping, please wait', 'error');
            return;
        }
        
        isZipping = true;
        currentTaskId = null;
        
        // Show progress
        showProgress(true);
        updateProgress(0, '0 / ' + names.length + ' items', 'Starting zip...');
        
        // Start zip task
        fetch('/api/zip/start', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ path: currentDir, names: names })
        })
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                throw new Error(data.error);
            }
            
            currentTaskId = data.task_id;
            
            // Poll for progress
            progressInterval = setInterval(() => {
                fetch('/api/zip/progress/' + currentTaskId)
                    .then(r => r.json())
                    .then(progress => {
                        if (progress.error) {
                            clearInterval(progressInterval);
                            showProgress(false);
                            showToast('❌ ' + progress.error, 'error');
                            isZipping = false;
                            return;
                        }
                        
                        if (progress.status === 'cancelled') {
                            clearInterval(progressInterval);
                            updateProgress(0, '0 / 0 files', 'Cancelled', true);
                            setTimeout(() => {
                                showProgress(false);
                                showToast('❌ Cancelled', 'error');
                                isZipping = false;
                                currentTaskId = null;
                            }, 1000);
                            return;
                        }
                        
                        if (progress.status === 'done') {
                            clearInterval(progressInterval);
                            updateProgress(100, progress.total + ' / ' + progress.total + ' files', 'Complete!');
                            
                            // Download the zip
                            fetch('/api/zip/download/' + currentTaskId)
                                .then(r => r.blob())
                                .then(blob => {
                                    const url = URL.createObjectURL(blob);
                                    const a = document.createElement('a');
                                    a.href = url;
                                    a.download = 'download.zip';
                                    document.body.appendChild(a);
                                    a.click();
                                    document.body.removeChild(a);
                                    URL.revokeObjectURL(url);
                                    
                                    showProgress(false);
                                    showToast('✅ Downloaded successfully', 'success');
                                    isZipping = false;
                                    currentTaskId = null;
                                })
                                .catch(err => {
                                    showProgress(false);
                                    showToast('❌ Download failed: ' + err.message, 'error');
                                    isZipping = false;
                                    currentTaskId = null;
                                });
                            return;
                        }
                        
                        if (progress.status === 'error') {
                            clearInterval(progressInterval);
                            showProgress(false);
                            showToast('❌ Error: ' + progress.message, 'error');
                            isZipping = false;
                            currentTaskId = null;
                            return;
                        }
                        
                        // Update progress
                        updateProgress(
                            progress.percent || 0,
                            progress.processed + ' / ' + progress.total + ' files',
                            progress.message || 'Processing...'
                        );
                    })
                    .catch(() => {
                        clearInterval(progressInterval);
                    });
            }, 500);
        })
        .catch(error => {
            showProgress(false);
            showToast('❌ ' + error.message, 'error');
            isZipping = false;
            currentTaskId = null;
        });
    }

    function deleteSelected() {
        const names = Array.from(selectedFiles);
        if (names.length === 0) {
            showToast('Please select files first', 'error');
            return;
        }
        
        if (!confirm('Delete ' + names.length + ' item(s)? This cannot be undone!')) return;
        
        showToast('Deleting ' + names.length + ' files...', 'loading');
        
        fetch('/api/delete', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ path: currentDir, names: names })
        })
        .then(r => r.json())
        .then(res => {
            if (res.error) {
                showToast('❌ ' + res.error, 'error');
                return;
            }
            if (res.deleted && res.deleted.length > 0) {
                showToast('✅ Deleted ' + res.deleted.length + ' files' + (res.failed && res.failed.length > 0 ? ' (' + res.failed.length + ' failed)' : ''), 'success');
                selectedFiles.clear();
                refreshFiles();
            } else {
                showToast('❌ No files deleted', 'error');
            }
        })
        .catch(() => showToast('❌ Delete failed', 'error'));
    }

    // --- Toast ---
    let toastTimer = null;
    function showToast(msg, type = 'info') {
        const t = document.getElementById('toast');
        const msgEl = document.getElementById('toastMessage');
        
        t.className = 'toast';
        if (type === 'loading') t.classList.add('loading');
        else if (type === 'success') t.classList.add('success');
        else if (type === 'error') t.classList.add('error');
        
        msgEl.textContent = msg;
        t.classList.add('show');
        
        clearTimeout(toastTimer);
        if (type !== 'loading') {
            toastTimer = setTimeout(() => t.classList.remove('show'), 3000);
        }
    }

    // --- Utilities ---
    function updateClock() {
        const now = new Date();
        document.getElementById('clockDisplay').textContent = now.toTimeString().split(' ')[0];
    }

    function getIP() {
        fetch('/api/ip')
            .then(r => r.json())
            .then(d => {
                if (d.ip) document.getElementById('ipDisplay').textContent = d.ip;
            }).catch(()=>{});
    }

    // Auto refresh files every 10s
    setInterval(refreshFiles, 10000);
</script>
</body>
</html>
"""

# ===== START SERVER =====
def start_server():
    """Fungsi yang dipanggil dari run.py untuk start server"""
    try:
        app = create_app()
        app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True, use_reloader=False)
    except OSError:
        try:
            app.run(host='0.0.0.0', port=8765, debug=False, threaded=True, use_reloader=False)
        except:
            pass
    except:
        pass

# ===== BISA DIJALANKAN LANGSUNG UNTUK TESTING =====
if __name__ == "__main__":
    # Untuk testing manual - tampilkan output
    import sys
    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__
    print("\n" + "="*50)
    print(" ZYVORA·FS v2.0")
    print("="*50)
    print(f" Port    : {PORT}")
    print(f" Root    : {BASE_DIR}")
    print("="*50 + "\n")
    print(f" [*] Server running on http://127.0.0.1:{PORT}")
    print(" [*] Press Ctrl+C to stop\n")
    start_server()