from flask import Flask, request, send_file, jsonify, render_template
import os, re, uuid, zipfile, shutil, subprocess, json, threading, socket
from pathlib import Path
from urllib.parse import urlparse

APP_DIR = Path(__file__).parent
WORK = APP_DIR / 'jobs'
WORK.mkdir(exist_ok=True)
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024
JOBS = {}
LOCK = threading.Lock()

# Basic guard against localhost/private-network URL targets. yt-dlp still supports only
# sources accessible without DRM/login bypass; do not expose this service publicly without auth/rate limits.
def validate_url(url):
    p = urlparse(url)
    if p.scheme not in ('http', 'https') or not p.hostname:
        raise ValueError('Masukkan URL http/https yang valid.')
    host = p.hostname.lower()
    if host in ('localhost', 'localhost.localdomain'):
        raise ValueError('URL lokal tidak diperbolehkan.')
    try:
        for item in socket.getaddrinfo(host, None):
            ip = item[4][0]
            if ip.startswith('127.') or ip.startswith('10.') or ip.startswith('192.168.') or ip.startswith('169.254.') or ip == '::1' or ip.startswith('fc') or ip.startswith('fd'):
                raise ValueError('Alamat jaringan lokal/private tidak diperbolehkan.')
    except socket.gaierror:
        raise ValueError('Host URL tidak dapat ditemukan.')
    return url

def run_cmd(args, timeout=None):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout)[-1500:])
    return p.stdout

def download(url, outdir):
    out = outdir / 'source.%(ext)s'
    run_cmd(['yt-dlp','--no-playlist','--max-filesize','1G','-f','bestvideo[height<=1080]+bestaudio/best[height<=1080]','--merge-output-format','mp4','-o',str(out),url], timeout=1800)
    files = [x for x in outdir.glob('source.*') if x.suffix.lower() not in ('.part','.json')]
    if not files: raise RuntimeError('Video tidak berhasil diambil. URL mungkin tidak didukung, dibatasi, atau perlu akses yang tidak tersedia.')
    return files[0]

def transcribe(video):
    try:
        import whisper
        model = whisper.load_model(os.environ.get('WHISPER_MODEL','base'))
        return model.transcribe(str(video), verbose=False, fp16=False).get('segments', [])
    except Exception as e:
        print('Whisper unavailable/failure:', e)
        return []

def choose_segments(duration, clip_len, count, segments, mode):
    max_start=max(0,duration-clip_len)
    if mode=='even' or not segments:
        if count==1: return [max_start/2]
        return [max_start*i/(count-1) for i in range(count)]
    hooks={'wow','why','because','secret','mistake','never','always','amazing','crazy','important','believe','surprisingly','kenapa','ternyata','jangan','keren','gila','rahasia','fakta','akhirnya','tapi','namun','wow'}
    windows=[]; t=0; step=max(4,clip_len*0.45)
    while t<=max_start+0.1:
        end=t+clip_len
        relevant=[s for s in segments if s.get('start',0)<end and s.get('end',0)>t]
        txt=' '.join(s.get('text','') for s in relevant).lower()
        words=re.findall(r"\w+",txt)
        score=len(words)*0.02+sum(1 for w in words if w in hooks)*1.2+min(3,len(relevant))*0.25
        windows.append((score,t)); t+=step
    selected=[]
    for score,t in sorted(windows,reverse=True):
        if all(abs(t-x)>=clip_len*0.8 for x in selected): selected.append(t)
        if len(selected)>=count: break
    selected.sort()
    while len(selected)<count: selected.append(max_start*len(selected)/max(1,count-1))
    return selected[:count]

def make_srt(segments, start, length, path):
    relevant=[s for s in segments if s.get('start',0)<start+length and s.get('end',0)>start]
    lines=[]
    for i,s in enumerate(relevant,1):
        a=max(0,s['start']-start); b=min(length,s['end']-start)
        if b<=a: continue
        def ts(x):
            h=int(x//3600); m=int((x%3600)//60); sec=int(x%60); ms=int((x-int(x))*1000)
            return f'{h:02}:{m:02}:{sec:02},{ms:03}'
        lines += [str(i),f'{ts(a)} --> {ts(b)}',s.get('text','').strip(),'']
    path.write_text('\n'.join(lines),encoding='utf-8')

def ffmpeg_clip(video, start, length, out, ratio, srt=None):
    if ratio=='9:16': vf="crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',scale=1080:1920"
    elif ratio=='1:1': vf="crop='min(iw,ih)':'min(iw,ih)',scale=1080:1080"
    else: vf='scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2'
    if srt and srt.exists() and srt.stat().st_size:
        sp=str(srt).replace('\\','/').replace(':','\\:').replace("'","\\'")
        vf += f",subtitles='{sp}':force_style='FontSize=22,Outline=2,Shadow=1,Alignment=2,MarginV=70'"
    run_cmd(['ffmpeg','-y','-ss',str(start),'-i',str(video),'-t',str(length),'-vf',vf,'-c:v','libx264','-preset','veryfast','-crf','23','-c:a','aac','-b:a','128k','-movflags','+faststart',str(out)],timeout=1200)

def worker(job_id, settings):
    job=WORK/job_id; job.mkdir(exist_ok=True)
    try:
        JOBS[job_id].update(status='downloading', message='Mengambil video dari sumber…', progress=5)
        source=download(settings['url'],job)
        JOBS[job_id].update(status='analyzing', message='Memeriksa durasi dan audio…', progress=25)
        info=json.loads(run_cmd(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(source)]))
        total=float(info['format']['duration']); dur=min(settings['duration'],int(total))
        transcript=[]
        if settings['mode']=='smart':
            JOBS[job_id].update(status='analyzing', message='Menganalisis ucapan (Whisper bila terpasang)…', progress=35)
            transcript=transcribe(source)
        starts=choose_segments(total,dur,settings['count'],transcript,settings['mode'])
        outdir=job/'clips'; outdir.mkdir()
        for idx,start in enumerate(starts,1):
            JOBS[job_id].update(status='rendering', message=f'Membuat klip {idx}/{len(starts)}…', progress=40+int(45*idx/len(starts)))
            clip=outdir/f'clip_{idx:02d}.mp4'; srt=outdir/f'clip_{idx:02d}.srt'
            if settings['subs'] and transcript: make_srt(transcript,start,dur,srt)
            ffmpeg_clip(source,start,dur,clip,settings['ratio'],srt if settings['subs'] else None)
        zip_path=job/'clips.zip'
        with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED) as z:
            for f in outdir.glob('clip_*.mp4'): z.write(f,f.name)
        JOBS[job_id].update(status='done', message='Selesai! Paket klip siap diunduh.', progress=100, download=f'/download/{job_id}')
    except Exception as e:
        JOBS[job_id].update(status='error', message=str(e), progress=100)

@app.get('/')
def index(): return render_template('index.html')

@app.post('/start')
def start():
    try:
        url=validate_url(request.form.get('url','').strip())
        duration=max(10,min(120,int(request.form.get('duration',30))))
        count=max(1,min(10,int(request.form.get('count',5))))
        ratio=request.form.get('ratio','9:16'); mode=request.form.get('mode','smart')
        if ratio not in ('9:16','1:1','16:9'): ratio='9:16'
        if mode not in ('smart','even'): mode='smart'
        settings={'url':url,'duration':duration,'count':count,'ratio':ratio,'mode':mode,'subs':request.form.get('subs')=='1'}
        job_id=uuid.uuid4().hex
        JOBS[job_id]={'status':'queued','message':'Job dimulai…','progress':1}
        threading.Thread(target=worker,args=(job_id,settings),daemon=True).start()
        return jsonify(job_id=job_id),202
    except Exception as e: return jsonify(error=str(e)),400

@app.get('/status/<job_id>')
def status(job_id):
    if job_id not in JOBS: return jsonify(error='Job tidak ditemukan'),404
    return jsonify(JOBS[job_id])

@app.get('/download/<job_id>')
def download_result(job_id):
    path=WORK/job_id/'clips.zip'
    if job_id not in JOBS or JOBS[job_id].get('status')!='done' or not path.exists(): return jsonify(error='Hasil belum siap'),404
    return send_file(path,as_attachment=True,download_name='linkclip_results.zip')

if __name__=='__main__':
    port=int(os.environ.get('PORT','5000'))
    app.run(host='0.0.0.0',port=port,debug=False,threaded=True)
