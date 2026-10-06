"""Validate the CPU container workflow in an isolated, disposable Docker volume."""
import os
import subprocess
import uuid


SMOKE = r'''
from io import BytesIO
import time, zipfile
import httpx, numpy as np, soundfile as sf
c=httpx.Client(base_url='http://127.0.0.1:8000',timeout=30)
for _ in range(100):
    try:
        if c.get('/api/health').status_code==200:break
    except httpx.ConnectError:pass
    time.sleep(.1)
assert c.get('/').status_code==200
stream=BytesIO();sr=16000;t=np.arange(sr*3)/sr
sf.write(stream,np.r_[.1*np.sin(2*np.pi*440*t),np.zeros(sr),.1*np.sin(2*np.pi*330*t)],sr,format='WAV')
r=c.post('/api/sources',files={'file':('container-smoke.wav',stream.getvalue())});r.raise_for_status();source=r.json()['id']
r=c.post('/api/datasets/prepare',json={'source_ids':[source]});r.raise_for_status();job=r.json()['id']
for _ in range(200):
    state=c.get('/api/jobs/'+job).json()
    if state['status'] not in ('queued','running'):break
    time.sleep(.1)
assert state['status']=='completed',state
mid=state['metadata']['result_id'];data=c.get('/api/datasets/'+mid).json();assert len(data['clips'])==2
r=c.post('/api/datasets/'+mid+'/review',json={'decisions':{clip['artifact_id']:'accepted' for clip in data['clips']}});r.raise_for_status();reviewed=r.json()['id']
r=c.post('/api/datasets/'+reviewed+'/export',json={});r.raise_for_status();job=r.json()['id']
for _ in range(200):
    state=c.get('/api/jobs/'+job).json()
    if state['status'] not in ('queued','running'):break
    time.sleep(.1)
assert state['status']=='completed',state
content=c.get('/api/artifacts/'+state['metadata']['result_id']+'/file').content
with zipfile.ZipFile(BytesIO(content)) as archive:assert len(archive.namelist())==3
assert not c.get('/api/engines').json()['rvc']['ready']
cleanup=c.post('/api/cache/cleanup',json={});cleanup.raise_for_status()
for clip in data['clips']:assert c.get('/api/artifacts/'+clip['artifact_id']+'/file').status_code==200
print('CPU container smoke passed: web, upload, worker, slicing, review, ZIP, cleanup protection, engine status')
'''


def main():
    suffix = uuid.uuid4().hex[:12]
    api, worker, volume = [f"voice-workbench-smoke-{kind}-{suffix}" for kind in ("api", "worker", "data")]
    def docker(*args, capture=False):
        try:
            return subprocess.run(["docker", *args], check=True, capture_output=capture, text=True)
        except subprocess.CalledProcessError as error:
            if capture and error.stderr:
                print(error.stderr)
            raise
    docker("volume", "create", volume, capture=True)
    try:
        docker("run", "--detach", "--name", api, "--volume", f"{volume}:/app/runtime", "voice-workbench:dev", capture=True)
        docker("run", "--detach", "--name", worker, "--volume", f"{volume}:/app/runtime", "voice-workbench:dev", "python", "-m", "voice_workbench_worker", capture=True)
        docker("exec", api, "python", "-c", SMOKE)
    finally:
        subprocess.run(["docker", "rm", "--force", api, worker], capture_output=True)
        subprocess.run(["docker", "volume", "rm", volume], capture_output=True)


if __name__ == "__main__":
    main()
