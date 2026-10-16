from pathlib import Path
import io
import json
import tempfile
import unittest
import urllib.error
from unittest.mock import patch


class VisionTests(unittest.TestCase):
    def test_match_requires_geometry_evidence(self):
        from common.vision import validate
        data=dict(complete=True,confidence=.95,reason='形状和孔位一致',evidence=['外轮廓一致','孔位分布一致'],same_object=True)
        self.assertEqual(validate('correspondence',data)['status'],'passed')
        data['confidence']=.5
        observed=validate('correspondence',data)
        self.assertEqual(observed['status'],'unknown')
        self.assertIn('0.50低于0.85',observed['reason'])

    def test_complexity_is_not_a_supported_llm_task(self):
        from common.vision import validate,PROMPTS
        data=dict(complete=True,confidence=.95,reason='存在多方向孔',evidence=['两个正交方向孔轴'],features=['分层及多轴孔'])
        self.assertEqual(validate('complexity',data)['status'],'unknown')
        self.assertNotIn('complexity',PROMPTS)

    def test_drawing_requires_two_views_and_dimensions(self):
        from common.vision import validate
        data=dict(complete=True,confidence=.95,reason='有尺寸和两个投影',evidence=['直径标注','正视和侧视'],has_dimensions=True,view_count=2,same_part=True)
        self.assertEqual(validate('drawing_quality',data)['status'],'passed')
        data['has_dimensions']=False
        self.assertEqual(validate('drawing_quality',data)['status'],'failed')

    def test_incomplete_and_invalid_never_pass(self):
        from common.vision import validate
        for data in ({},dict(complete=False,confidence=1,reason='未看清',evidence=[],same_object=True)):
            self.assertEqual(validate('correspondence',data)['status'],'unknown')

    def test_off_mode_does_not_read_config(self):
        from common.vision import Judge
        with tempfile.TemporaryDirectory() as d:
            judge=Judge(Path(d)/'missing',Path(d)/'cache',mode='off')
            self.assertEqual(judge.assess('correspondence',[],{})['status'],'unknown')
            self.assertEqual(judge.calls,0)


class JudgeRequestTests(unittest.TestCase):
    def setUp(self):
        from common.vision import Judge
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root=Path(self.directory.name)
        config=root/'config.json'
        config.write_text(json.dumps(dict(base_url='https://model.example/v1',api_key='test-secret')))
        self.image=root/'view.png'
        self.image.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
        self.judge=Judge(config,root/'cache',max_calls=2)
        self.valid=dict(complete=True,confidence=.95,reason='形状和孔位一致',
                        evidence=['外轮廓一致','孔位分布一致'],same_object=True)

    def response(self,finish='stop',content=None):
        return io.BytesIO(json.dumps(dict(choices=[dict(finish_reason=finish,
            message=dict(content=json.dumps(self.valid) if content is None else content))],
            usage=dict(completion_tokens=2500,prompt_tokens=2690,total_tokens=5190,
                       completion_tokens_details=dict(reasoning_tokens=2500)))).encode())

    def assess(self):
        return self.judge.assess('correspondence',[self.image],{})

    @patch('common.vision.urllib.request.build_opener')
    def test_reasoning_model_has_room_for_final_json(self,build):
        from common.vision import MODEL
        def reply(request,timeout):
            payload=json.loads(request.data)
            self.assertEqual(payload['model'],MODEL)
            # Reproduce a reasoning response consuming the entire old allowance.
            return self.response('length','') if payload['max_tokens']<=2500 else self.response()
        build.return_value.open.side_effect=reply
        self.assertEqual(self.assess()['status'],'passed')
        self.assertEqual(self.judge.calls,1)
        self.assertEqual(json.loads(build.return_value.open.call_args.args[0].data)['max_tokens'],16000)

    @patch('common.vision.urllib.request.build_opener')
    def test_length_even_with_valid_json_is_unknown_and_not_cached(self,build):
        build.return_value.open.side_effect=[self.response('length'),self.response()]
        observed=self.assess()
        self.assertEqual(observed['status'],'unknown')
        self.assertIn('输出令牌预算',observed['reason'])
        self.assertFalse(self.judge.cache.exists())
        self.assertEqual(self.judge.calls,1)
        self.assertEqual(self.assess()['status'],'passed')
        self.assertEqual(self.judge.calls,2)

    @patch('common.vision.urllib.request.build_opener')
    def test_valid_response_cache_avoids_network_even_after_budget(self,build):
        self.judge.max_calls=1
        build.return_value.open.return_value=self.response()
        self.assertEqual(self.assess()['status'],'passed')
        self.assertEqual(self.assess()['status'],'passed')
        self.assertEqual(self.judge.calls,1)
        self.assertEqual(self.judge.cache_hits,1)

    @patch('common.vision.urllib.request.build_opener')
    def test_truncated_response_does_not_retry_or_exceed_call_budget(self,build):
        self.judge.max_calls=1
        build.return_value.open.return_value=self.response('length','')
        self.assertEqual(self.assess()['status'],'unknown')
        observed=self.assess()
        self.assertEqual(observed['status'],'unknown')
        self.assertIn('调用预算已用尽',observed['reason'])
        self.assertEqual(build.return_value.open.call_count,1)

    @patch('common.vision.urllib.request.build_opener')
    def test_network_failure_is_unknown_without_sensitive_details(self,build):
        build.return_value.open.side_effect=RuntimeError('test-secret raw response')
        observed=self.assess()
        self.assertEqual(observed['status'],'unknown')
        self.assertNotIn('test-secret',json.dumps(observed))
        self.assertNotIn('raw response',json.dumps(observed))
        self.assertEqual(self.judge.calls,1)

    @patch('common.vision.urllib.request.build_opener')
    def test_invalid_final_json_is_not_cached(self,build):
        build.return_value.open.return_value=self.response(content='{"complete": true}')
        self.assertEqual(self.assess()['status'],'unknown')
        self.assertFalse(self.judge.cache.exists())

    @patch('common.vision.urllib.request.build_opener')
    def test_default_timeout_allows_two_minutes_for_reasoning(self,build):
        build.return_value.open.return_value=self.response()
        self.assertEqual(self.assess()['status'],'passed')
        self.assertEqual(build.return_value.open.call_args.kwargs['timeout'],120)

    @patch('common.vision.urllib.request.build_opener')
    def test_direct_and_wrapped_timeout_have_safe_specific_reason(self,build):
        for error in (TimeoutError('test-secret model.example'),
                      urllib.error.URLError(TimeoutError('test-secret model.example'))):
            with self.subTest(error_type=type(error).__name__):
                build.return_value.open.side_effect=error
                observed=self.assess()
                self.assertEqual(observed['status'],'unknown')
                self.assertIn('请求超时',observed['reason'])
                self.assertNotIn('test-secret',observed['reason'])
                self.assertNotIn('model.example',observed['reason'])
                self.assertFalse(self.judge.cache.exists())
        self.assertEqual(self.judge.calls,2)

    @patch('common.vision.urllib.request.build_opener')
    def test_http_failure_reports_only_status_code(self,build):
        for code in (401,429,500):
            with self.subTest(code=code):
                self.judge.calls=0
                build.return_value.open.side_effect=urllib.error.HTTPError(
                    'https://model.example/test-secret',code,'test-secret',{},None)
                observed=self.assess()
                self.assertEqual(observed['status'],'unknown')
                self.assertIn(f'HTTP {code}',observed['reason'])
                self.assertNotIn('test-secret',observed['reason'])
                self.assertNotIn('model.example',observed['reason'])
                self.assertEqual(self.judge.calls,1)

    @patch('common.vision.urllib.request.build_opener')
    def test_url_failure_is_distinct_from_timeout_without_host(self,build):
        build.return_value.open.side_effect=urllib.error.URLError('test-secret model.example')
        observed=self.assess()
        self.assertEqual(observed['status'],'unknown')
        self.assertIn('网络连接失败',observed['reason'])
        self.assertNotIn('请求超时',observed['reason'])
        self.assertNotIn('test-secret',observed['reason'])
        self.assertNotIn('model.example',observed['reason'])

    @patch('common.vision.urllib.request.build_opener')
    def test_response_json_syntax_failure_does_not_expose_body(self,build):
        for response in (io.BytesIO(b'test-secret model.example'),
                         self.response(content='test-secret model.example')):
            build.return_value.open.return_value=response
            observed=self.assess()
            self.assertEqual(observed['status'],'unknown')
            self.assertIn('模型响应JSON解析失败',observed['reason'])
            self.assertNotIn('test-secret',observed['reason'])
            self.assertNotIn('model.example',observed['reason'])
            self.assertFalse(self.judge.cache.exists())

    @patch('common.vision.urllib.request.build_opener')
    def test_config_json_failure_is_not_mislabeled_as_model_response(self,build):
        self.judge.config.write_text('{test-secret')
        observed=self.assess()
        self.assertEqual(observed['status'],'unknown')
        self.assertIn('模型配置JSON解析失败',observed['reason'])
        self.assertNotIn('test-secret',observed['reason'])
        self.assertEqual(self.judge.calls,0)
        build.assert_not_called()


if __name__=='__main__':
    unittest.main()
