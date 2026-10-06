import ast,json,pathlib,unittest
from types import SimpleNamespace
from unittest.mock import Mock
P=pathlib.Path(__file__).resolve().parents[1] / 'daly_ha_bridge.py'
def load_functions():
    tree=ast.parse(P.read_text())
    selected=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('publish_discovery','publish_temperature_measurements')]
    client=Mock()
    env=dict(json=json,client=client,LOG=Mock(),read_optional=lambda method,description:method(),
             state_topic=lambda key:'test/state/'+key,AVAILABILITY_TOPIC='test/availability',
             CELL_COUNT=14,CHARGE_COMMAND_TOPIC='test/charge',DISCHARGE_COMMAND_TOPIC='test/discharge',
             DEVICE_INFO={'identifiers':['test']},DISCOVERY_PREFIX='homeassistant')
    exec(compile(ast.Module(body=selected,type_ignores=[]),str(P),'exec'),env)
    return env
class Tests(unittest.TestCase):
    def test_discovery_temperature_metadata(self):
        e=load_functions();e['publish_discovery']()
        configs={call.args[0]:json.loads(call.args[1]) for call in e['client'].publish.call_args_list}
        for key in ('highest_temperature','lowest_temperature'):
            c=configs['homeassistant/sensor/daly_bms/'+key+'/config']
            self.assertEqual(c['unit_of_measurement'],'\u00b0C')
            self.assertEqual(c['device_class'],'temperature')
            self.assertEqual(c['state_class'],'measurement')
            self.assertEqual(c['expire_after'],180)
        self.assertNotIn('expire_after',configs['homeassistant/sensor/daly_bms/soc/config'])
    def test_valid_values_are_not_retained(self):
        e=load_functions()
        e['publish_temperature_measurements'](SimpleNamespace(get_temperature_range=lambda:{'lowest_temperature':22,'highest_temperature':27}))
        calls=e['client'].publish.call_args_list
        self.assertEqual([c.args for c in calls],[('test/state/highest_temperature','27'),('test/state/lowest_temperature','22')])
        self.assertTrue(all(c.kwargs=={'qos':1,'retain':False} for c in calls))
    def test_zero_and_negative_temperatures(self):
        e=load_functions()
        e['publish_temperature_measurements'](SimpleNamespace(get_temperature_range=lambda:{'lowest_temperature':-5,'highest_temperature':0}))
        self.assertEqual(e['client'].publish.call_count,2)
    def test_missing_read_no_publish(self):
        for result in (None,False,{},[]):
            e=load_functions()
            e['publish_temperature_measurements'](SimpleNamespace(get_temperature_range=lambda:result))
            e['client'].publish.assert_not_called()
    def test_invalid_read_no_publish(self):
        for low,high in ((30,20),('22',24),(True,24),(0,float('nan')),(-41,20),(20,88)):
            e=load_functions()
            e['publish_temperature_measurements'](SimpleNamespace(get_temperature_range=lambda:{'lowest_temperature':low,'highest_temperature':high}))
            e['client'].publish.assert_not_called()
    def test_temperature_path_has_no_setters_or_cache_writes(self):
        tree=ast.parse(P.read_text())
        helper=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='publish_temperature_measurements')
        attrs={n.attr for n in ast.walk(helper) if isinstance(n,ast.Attribute)}
        self.assertFalse(any(x.startswith('set_') for x in attrs))
        names={n.id for n in ast.walk(helper) if isinstance(n,ast.Name)}
        self.assertNotIn('state_cache',names)
        loop=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='publish_measurements')
        self.assertEqual(sum(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='publish_temperature_measurements' for n in ast.walk(loop)),1)
if __name__=='__main__':unittest.main()
