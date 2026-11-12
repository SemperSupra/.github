from pathlib import Path
import json
Path('output').mkdir(exist_ok=True)
Path('output/result.json').write_text(json.dumps({'schema':'semper-cloud-result/v1','provider':'colab','status':'PASS','synthetic':True})+'\n')
