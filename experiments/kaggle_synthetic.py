from pathlib import Path
import json
out=Path('/kaggle/working') if Path('/kaggle/working').is_dir() else Path.cwd()
(out/'output').mkdir(exist_ok=True)
(out/'output'/'result.json').write_text(json.dumps({'schema':'semper-cloud-result/v1','provider':'kaggle','status':'PASS','synthetic':True})+'\n')
