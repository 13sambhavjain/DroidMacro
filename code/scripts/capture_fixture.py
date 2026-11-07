"""Capture a real a11y screen to a JSON fixture, so predicate tests run offline."""
import json, os, pathlib, sys, time
os.environ.setdefault('GRPC_VERBOSITY', 'ERROR')
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from android_world.env import env_launcher, json_action
from android_world import registry
from flowagent.config import ADB, CONSOLE_PORT, GRPC_PORT

task_name, app, out = sys.argv[1], sys.argv[2], sys.argv[3]
env = env_launcher.load_and_setup_env(console_port=CONSOLE_PORT, emulator_setup=False,
    adb_path=ADB, grpc_port=GRPC_PORT)
env.reset(go_home=True)
aw = registry.TaskRegistry().get_registry('android_world')
T = aw[task_name]; params = T.generate_random_params(); t = T(params); t.initialize_task(env)
env.execute_action(json_action.JSONAction(action_type='open_app', app_name=app))
time.sleep(3)
els = env.get_state(wait_to_stabilize=True).ui_elements
data = {'task': task_name, 'goal': t.goal, 'params': {k: str(v) for k, v in params.items()},
        'elements': [{'resource_name': e.resource_name, 'content_description': e.content_description,
                      'text': e.text, 'class_name': e.class_name,
                      'is_clickable': e.is_clickable, 'is_editable': e.is_editable}
                     for e in els]}
json.dump(data, open(out, 'w'), indent=1)
print(f'wrote {out}: {len(els)} elements; goal={t.goal}')
t.tear_down(env); env.close()
