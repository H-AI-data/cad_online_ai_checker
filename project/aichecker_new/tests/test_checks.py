import importlib
import unittest


def assess(module,dimension,ctx):
    return importlib.import_module(f'modules.module_{module:02d}.{dimension}.checker').assess(ctx)


class ChecksTests(unittest.TestCase):
    def test_module2_thresholds_count_unique_families(self):
        ctx={'stats':dict(complete=True,total=50,nonstandard=15,standard=35)}
        dim='dimension_02_quantity_ratio'
        self.assertEqual(assess(2,dim,ctx)['status'],'passed')
        ctx['stats']['nonstandard']=14;ctx['stats']['standard']=36
        self.assertEqual(assess(2,dim,ctx)['status'],'failed')
        ctx['stats'].update(total=49,standard=34,nonstandard=15)
        self.assertEqual(assess(2,dim,ctx)['status'],'failed')

    def test_module3_100_inclusive(self):
        ctx={'stats':dict(complete=True,total=99)}
        self.assertEqual(assess(3,'dimension_02_unique_count',ctx)['status'],'failed')
        ctx['stats']['total']=100
        self.assertEqual(assess(3,'dimension_02_unique_count',ctx)['status'],'passed')
        ctx['stats']['complete']=False
        self.assertEqual(assess(3,'dimension_02_unique_count',ctx)['status'],'unknown')

    def test_complexity_uses_new_pass_semantics(self):
        ctx={'complexity':{'status':'passed','reason':'通过','evidence':{'features':['密集齿形']}}}
        self.assertEqual(assess(1,'dimension_02_complexity',ctx)['status'],'passed')

    def test_membership_incomplete_foreign_is_unknown(self):
        ctx={'membership':dict(complete=False,foreign=['外来.step'],ambiguous=['未知.step']), 'membership_visual':[]}
        self.assertEqual(assess(2,'dimension_03_membership',ctx)['status'],'unknown')

    def test_membership_confirmed_foreign_fails(self):
        ctx={'membership':dict(complete=True,foreign=['外来.step'],ambiguous=[]), 'membership_visual':[]}
        self.assertEqual(assess(2,'dimension_03_membership',ctx)['status'],'failed')

    def test_pair_counts_mismatch_never_calls_judge(self):
        from common.checks import pair_documents
        class Judge:
            def assess(self,*args):
                raise AssertionError('数量不等时不应调用LLM')
        result=pair_documents(['a','b'],['x'],{}, {},Judge())
        self.assertEqual(result['status'],'failed')

    def test_equal_counts_do_not_prove_correspondence(self):
        from common.checks import pair_documents
        class Judge:
            def assess(self,*args):
                return dict(status='failed',reason='形状不一致',evidence={})
        result=pair_documents(['a.step'],['a.pdf'],{'a.step':dict(complete=True,images=['model.png'],families=[])},
            {'a.pdf':dict(complete=True,images=['drawing.png'],pages=[])},Judge())
        self.assertEqual(result['status'],'failed')
        self.assertIn('形状不一致',result['reason'])

    def test_correspondence_sends_actual_geometry_scale(self):
        from common.checks import pair_documents
        observed=[]
        class Judge:
            def assess(self,task,images,evidence):
                observed.append(evidence)
                return dict(status='passed',reason='通过',evidence={})
        metrics=dict(length_unit='mm',world_axis_aligned_size=[300,25,300])
        pair_documents(['a.step'],['a.pdf'],
            {'a.step':dict(complete=True,images=['model.png'],families=[],geometry_evidence=metrics)},
            {'a.pdf':dict(complete=True,images=['drawing.png'],pages=[])},Judge())
        self.assertEqual(observed[0].get('geometry_evidence'),metrics)


if __name__=='__main__':
    unittest.main()
