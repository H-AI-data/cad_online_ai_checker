from pathlib import Path
import tempfile
import unittest


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def file(self,name):
        p=self.root/name
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text('example')
        return p

    def test_nested_single_pair(self):
        self.file('模型/a.STEP');self.file('图纸/更多/a.PDF')
        from common.inventory import discover
        data=discover(self.root)
        self.assertEqual(data['layout'],'single')
        self.assertEqual(len(data['steps']),1)
        self.assertEqual(len(data['drawings']),1)

    def test_two_named_roles(self):
        self.file('文件夹1-装配体的stp和PDF/a.step')
        self.file('文件夹2-零件的stp和PDF/a.step')
        from common.inventory import discover
        data=discover(self.root)
        self.assertEqual(data['layout'],'package')
        self.assertIn('文件夹1',data['assembly_dir'])

    def test_multiple_models_not_guessed(self):
        self.file('a.step');self.file('b.step')
        from common.inventory import discover
        self.assertEqual(discover(self.root)['layout'],'unknown')

    def test_symlink_and_hidden_not_followed(self):
        self.file('.cache/ignored.step')
        self.file('a.step');(self.root/'linked.step').symlink_to(self.root/'a.step')
        from common.inventory import discover
        data=discover(self.root)
        self.assertEqual(len(data['steps']),1)
        self.assertTrue(data['gaps'])


if __name__=='__main__':
    unittest.main()
