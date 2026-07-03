"""Sur .161 : deploie generate_dataset_120.py, genere le dataset 120-classes, ENTRAINE un modele
SEPARE (model_120.joblib, ne touche PAS le live), imprime la precision multi-classes."""
import paramiko, sys
KEY="C:/Users/houss/.ssh/id_ed25519"; AI="192.168.148.161"
GEN="C:/Users/houss/IdeaProjects/PFE-Orange/backend/backend/ai-service/scripts/generate_dataset_120.py"
APP="/opt/pfe-ai-service"
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
k=paramiko.Ed25519Key.from_private_key_file(KEY); c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(AI,username="kube",pkey=k,timeout=12,allow_agent=False,look_for_keys=False)
def run(cmd,t=600): _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
sftp=c.open_sftp(); sftp.put(GEN,"/tmp/generate_dataset_120.py"); sftp.close()
out("[0] deploy generateur + verif venv/scripts")
out(run(f"echo 'kube' | sudo -S bash -c 'cp /tmp/generate_dataset_120.py {APP}/scripts/generate_dataset_120.py; chown aiops:aiops {APP}/scripts/generate_dataset_120.py; ls {APP}/.venv/bin/python {APP}/scripts/train_baseline.py'"))
out("\n[1] generation dataset 120 (as aiops)")
out(run(f"echo 'kube' | sudo -S -u aiops bash -c 'cd {APP} && .venv/bin/python -m scripts.generate_dataset_120' 2>&1",t=300))
out("\n[2] entrainement modele SEPARE (model_120.joblib)")
cmd=(f"cd {APP} && .venv/bin/python -m scripts.train_baseline "
     f"--dataset datasets/aiops_dataset_120.csv "
     f"--output-model model_artifacts/model_120.joblib "
     f"--output-metrics model_artifacts/model_120_metrics.json 2>&1 | tail -n 40")
out(run(f"echo 'kube' | sudo -S -u aiops bash -c '{cmd}'",t=600))
out("\n[3] metrics (accuracy multi-classes)")
out(run(f"echo 'kube' | sudo -S -u aiops bash -c 'cd {APP} && grep -oE \"(accuracy|scenario|multiclass|macro|f1)[^,{{}}]*\" model_artifacts/model_120_metrics.json | head -n 20' 2>&1"))
out(run(f"echo 'kube' | sudo -S -u aiops bash -c 'ls -la {APP}/model_artifacts/model_120*'"))
c.close()
