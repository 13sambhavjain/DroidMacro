"""Survey which a11y attributes are actually populated + unique on real screens.

The verification predicate depends entirely on this: an attribute that is usually
None, or usually duplicated, cannot identify an element at replay time.
"""
import collections, os, pathlib, sys, time
os.environ.setdefault('GRPC_VERBOSITY', 'ERROR')
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from android_world.env import env_launcher, json_action
from flowagent.config import ADB, CONSOLE_PORT, GRPC_PORT

ATTRS = ['resource_id', 'resource_name', 'text', 'content_description',
         'class_name', 'hint_text', 'tooltip']

def survey(elements, label, out):
  n = len(elements)
  present = {a: 0 for a in ATTRS}
  values = {a: collections.Counter() for a in ATTRS}
  for e in elements:
    for a in ATTRS:
      v = getattr(e, a, None)
      if v not in (None, ''):
        present[a] += 1
        values[a][str(v)] += 1
  out.append((label, n, present, values))

def main():
  env = env_launcher.load_and_setup_env(
      console_port=CONSOLE_PORT, emulator_setup=False, adb_path=ADB, grpc_port=GRPC_PORT)
  env.reset(go_home=True)
  out = []
  survey(env.get_state(wait_to_stabilize=True).ui_elements, 'home', out)

  for app in ['Markor', 'Simple Calendar Pro', 'Simple SMS Messenger', 'Contacts']:
    try:
      env.execute_action(json_action.JSONAction(action_type='open_app', app_name=app))
      time.sleep(2)
      survey(env.get_state(wait_to_stabilize=True).ui_elements, app, out)
    except Exception as exc:
      print(f'[skip] {app}: {type(exc).__name__}: {exc}')
    env.execute_action(json_action.JSONAction(action_type='navigate_home'))
    time.sleep(1)

  print('\n' + '='*84)
  print(f'{"screen":<22}{"elems":>6}  ' + '  '.join(f'{a[:9]:>9}' for a in ATTRS))
  print('-'*84)
  tot_n = 0; tot_p = {a: 0 for a in ATTRS}
  for label, n, present, _ in out:
    tot_n += n
    for a in ATTRS: tot_p[a] += present[a]
    print(f'{label:<22}{n:>6}  ' + '  '.join(f'{present[a]:>9}' for a in ATTRS))
  print('-'*84)
  print(f'{"% populated":<22}{tot_n:>6}  ' +
        '  '.join(f'{100*tot_p[a]/max(tot_n,1):>8.0f}%' for a in ATTRS))

  print('\n=== UNIQUENESS (how often a non-null value identifies exactly ONE element) ===')
  for label, n, present, values in out:
    line = []
    for a in ATTRS:
      c = values[a]
      if not c: line.append(f'{a[:9]:>9}:  n/a'); continue
      uniq = sum(1 for v, k in c.items() if k == 1)
      line.append(f'{a[:9]:>9}:{100*uniq/len(c):>4.0f}%')
    print(f'{label:<22} ' + ' '.join(line))

  print('\n=== SAMPLE elements (Markor-like screen) ===')
  st = env.get_state(wait_to_stabilize=True)
  for e in st.ui_elements[:6]:
    print(f'  rid={e.resource_id!r:<45} text={e.text!r:<28} cd={e.content_description!r:<22} cls={(e.class_name or "").split(".")[-1]!r}')
  env.close()

if __name__ == '__main__':
  main()
