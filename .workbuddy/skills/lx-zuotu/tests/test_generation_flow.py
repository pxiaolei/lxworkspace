import json
import tempfile
import unittest
import threading
from unittest.mock import patch
from pathlib import Path

from PIL import Image, ImageDraw
from _support import FIXTURE, require
from test_validation import reviewed


class SyntheticProvider:
    name='synthetic-offline'
    submissions=0
    def __init__(self, opaque=False): self.calls=[]; self.opaque=opaque
    def generate(self, *, prompt, references, output_path, target, on_submitted):
        self.submissions+=1
        self.calls.append({'target':target['id'], 'references':list(references),'prompt':prompt})
        on_submitted(f'offline-{self.submissions}')
        image=Image.new('RGBA',(target['width'],target['height']),(0,0,0,0) if target.get('transparent') and not self.opaque else (50,100,200,255))
        if target.get('transparent') and not self.opaque:
            ImageDraw.Draw(image).rounded_rectangle((20,20,image.width-20,image.height-20),radius=30,fill=(50,100,200,255))
        image.save(output_path)
        return {'provider':self.name,'task_id':f'offline-{self.submissions}'}


class GenerationFlowTests(unittest.TestCase):
    def setUp(self):
        self.cli=require(self,'run_zuotu_batch')
        self.plan=require(self,'input_plan').build_plan(FIXTURE)

    def review_pending(self, root, manifest):
        images=[{'target_id':key,**reviewed(row)} for key,row in manifest['images'].items() if row['status']=='pending_review']
        p=root/'review.json'; p.write_text(json.dumps({'plan_hash':manifest['plan_hash'],'images':images},ensure_ascii=False))
        return self.cli.apply_reviews(root/'manifest.json',p)

    def test_sample_then_full_eight_physical_files_with_explicit_fake_reviews(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'activity'; adapter=SyntheticProvider()
            sample=self.cli.generate_batch(self.plan,root,sample_only=True,confirmed=True,adapter=adapter)
            self.assertEqual(len(adapter.calls),2)
            self.assertEqual(sum(r['status']=='pending_review' for r in sample['images'].values()),2)
            self.assertFalse((root/'产出图').exists())
            ready=self.review_pending(root,sample)
            full=self.cli.generate_batch(self.plan,root,confirmed=True,adapter=adapter)
            self.assertEqual(len(adapter.calls),8)
            self.assertEqual(full['provider'],'synthetic-offline')
            self.assertTrue(all(r['physical']['passed'] for r in full['images'].values()))
            done=self.review_pending(root,full)
            self.assertEqual(done['status'],'complete')
            self.assertEqual(len(list((root/'产出图').iterdir())),8)
            self.assertTrue(all(r['status']=='passed' for r in done['images'].values()))
            for call in adapter.calls[2:]: self.assertIn('临时图',str(call['references'][0]))

    def test_popup_scope_and_maximum_three_failed_attempts(self):
        plan=require(self,'input_plan').build_plan(FIXTURE,types='popup')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'popup'; adapter=SyntheticProvider(opaque=True)
            result=self.cli.generate_batch(plan,root,sample_only=True,confirmed=True,adapter=adapter)
            self.assertEqual(len(result['images']),2)
            self.assertEqual(len(adapter.calls),3)
            result=self.cli.generate_batch(plan,root,sample_only=True,confirmed=True,adapter=adapter)
            self.assertEqual(len(adapter.calls),3)

    def test_batch_requires_reviewed_sample_and_changed_content_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'activity'; adapter=SyntheticProvider()
            with self.assertRaises(ValueError): self.cli.generate_batch(self.plan,root,confirmed=True,adapter=adapter)
            self.assertFalse(root.exists())
            self.cli.generate_batch(self.plan,root,sample_only=True,confirmed=True,adapter=adapter)
            with self.assertRaises(ValueError): self.cli.generate_batch(self.plan,root,confirmed=True,adapter=adapter)
            with self.assertRaises(ValueError): self.cli.generate_batch({**self.plan,'plan_hash':'changed'},root,sample_only=True,confirmed=True,adapter=adapter)
            self.assertEqual(len(adapter.calls),2)

    def test_visual_rejection_retries_same_provider_and_preserves_attempts(self):
        plan=require(self,'input_plan').build_plan(FIXTURE,types='popup')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'activity'; adapter=SyntheticProvider()
            sample=self.cli.generate_batch(plan,root,sample_only=True,confirmed=True,adapter=adapter)
            row=next(r for r in sample['images'].values() if r['status']=='pending_review')
            review=reviewed(row); review['checks']['no_crop']=False
            p=root/'bad.json'; p.write_text(json.dumps({'plan_hash':sample['plan_hash'],'images':[{'target_id':row['target']['id'],**review}]}))
            self.cli.apply_reviews(root/'manifest.json',p)
            result=self.cli.generate_batch(plan,root,sample_only=True,confirmed=True,adapter=adapter)
            row=result['images'][row['target']['id']]
            self.assertEqual(len(row['attempts']),2)
            self.assertEqual(row['status'],'pending_review')
            self.assertFalse((root/'样张').exists())

    def test_uncertain_submission_is_not_automatically_resubmitted(self):
        api=require(self,'kie_adapter')
        class Unknown(SyntheticProvider):
            def generate(self,**kwargs):
                self.submissions+=1
                raise api.KIEAdapterError('KIE state unknown',uncertain=True)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'activity'; adapter=Unknown()
            result=self.cli.generate_batch(self.plan,root,sample_only=True,confirmed=True,adapter=adapter)
            count=adapter.submissions
            result=self.cli.generate_batch(self.plan,root,sample_only=True,confirmed=True,adapter=adapter)
            self.assertEqual(adapter.submissions,count)
            self.assertTrue(any(r['status']=='blocked' for r in result['images'].values()))

    def test_stale_candidate_and_existing_final_file_are_not_accepted_or_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'activity'; sample=self.cli.generate_batch(self.plan,root,sample_only=True,confirmed=True,adapter=SyntheticProvider())
            row=next(r for r in sample['images'].values() if r['status']=='pending_review')
            (root/row['candidate']).write_bytes(b'changed')
            result=self.review_pending(root,sample)
            self.assertEqual(result['images'][row['target']['id']]['status'],'failed_validation')

    def test_prompt_uses_current_text_and_no_splash_button(self):
        row=next(r for r in self.plan['targets'] if r['type']=='splash')
        prompt=self.cli.build_prompt(self.plan,row)
        self.assertIn('¥50',prompt)
        self.assertNotIn('69小时',prompt)
        self.assertIn('无按钮',prompt)

    def test_modified_approved_master_blocks_before_any_batch_write_or_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'activity'; adapter=SyntheticProvider()
            sample=self.cli.generate_batch(self.plan,root,sample_only=True,confirmed=True,adapter=adapter)
            ready=self.review_pending(root,sample)
            master=ready['images']['splash:1242x2208']
            (root/master['candidate']).write_bytes(b'changed master')
            with self.assertRaises(ValueError): self.cli.generate_batch(self.plan,root,confirmed=True,adapter=adapter)
            self.assertEqual(len(adapter.calls),2)
            self.assertFalse((root/'产出图').exists())

    def test_changed_source_after_plan_blocks_before_creating_activity(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'input.yaml'; source.write_bytes(FIXTURE.read_bytes())
            plan=require(self,'input_plan').build_plan(source)
            source.write_text(source.read_text().replace('¥50','¥500'))
            root=Path(tmp)/'activity'; adapter=SyntheticProvider()
            with self.assertRaises(ValueError): self.cli.generate_batch(plan,root,sample_only=True,confirmed=True,adapter=adapter)
            self.assertFalse(root.exists())
            self.assertEqual(adapter.submissions,0)

    def test_popup_full_flow_delivers_exactly_two_files(self):
        plan=require(self,'input_plan').build_plan(FIXTURE,types='popup')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'activity'; adapter=SyntheticProvider()
            sample=self.cli.generate_batch(plan,root,sample_only=True,confirmed=True,adapter=adapter)
            self.review_pending(root,sample)
            full=self.cli.generate_batch(plan,root,confirmed=True,adapter=adapter)
            done=self.review_pending(root,full)
            self.assertEqual(done['status'],'complete')
            self.assertEqual(len(list((root/'产出图').glob('*.png'))),2)
            self.assertEqual(adapter.submissions,2)

    def test_completed_candidate_is_recovered_without_resubmitting(self):
        plan=require(self,'input_plan').build_plan(FIXTURE,types='popup')
        class Resumable(SyntheticProvider):
            def resume(self,**kwargs): return {'provider':self.name,'task_id':kwargs['task_id']}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'activity'; adapter=Resumable(); process=self.cli.process_image
            def interrupted(*args):
                process(*args)
                raise KeyboardInterrupt('simulated interruption after successful encode')
            with patch.object(self.cli,'process_image',side_effect=interrupted),self.assertRaises(KeyboardInterrupt):
                self.cli.generate_batch(plan,root,sample_only=True,confirmed=True,adapter=adapter)
            result=self.cli.generate_batch(plan,root,sample_only=True,confirmed=True,adapter=adapter)
            row=next(r for r in result['images'].values() if r['status']!='not_generated')
            self.assertEqual(adapter.submissions,1)
            self.assertEqual(len(row['attempts']),1)
            self.assertEqual(row['status'],'pending_review')

    def test_concurrent_confirmed_run_is_rejected_without_touching_active_job(self):
        plan=require(self,'input_plan').build_plan(FIXTURE,types='home_banner')
        entered,release=threading.Event(),threading.Event()
        class Blocking(SyntheticProvider):
            def generate(self,**kwargs):
                result=super().generate(**kwargs)
                entered.set(); release.wait(5)
                return result
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'activity'; adapter=Blocking(); errors=[]
            def first():
                try: self.cli.generate_batch(plan,root,sample_only=True,confirmed=True,adapter=adapter)
                except Exception as exc: errors.append(exc)
            thread=threading.Thread(target=first); thread.start()
            try:
                self.assertTrue(entered.wait(3))
                with self.assertRaises(ValueError):
                    self.cli.generate_batch(plan,root,sample_only=True,confirmed=True,adapter=adapter)
            finally:
                release.set(); thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors,[])
            self.assertEqual(adapter.submissions,1)

    def test_full_cli_pending_visual_review_returns_three(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/self.plan['activity']['activity_name']; adapter=SyntheticProvider()
            sample=self.cli.generate_batch(self.plan,root,sample_only=True,confirmed=True,adapter=adapter)
            self.review_pending(root,sample)
            generate=self.cli.generate_batch
            with patch.object(self.cli,'OUTPUT_ROOT',Path(tmp)),patch.object(self.cli,'generate_batch',side_effect=lambda plan,path,**kwargs:generate(plan,path,adapter=adapter,**kwargs)):
                self.assertEqual(self.cli.main(['--confirmed','--file',str(FIXTURE)]),3)
            manifest=json.loads((root/'manifest.json').read_text())
            self.assertEqual(manifest['status'],'pending_review')

    def test_review_recovery_adopts_identical_completed_copy_without_overwrite(self):
        plan=require(self,'input_plan').build_plan(FIXTURE,types='home_banner')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'activity'; sample=self.cli.generate_batch(plan,root,sample_only=True,confirmed=True,adapter=SyntheticProvider())
            row=next(iter(sample['images'].values()))
            path=root/'review.json'; path.write_text(json.dumps({'plan_hash':sample['plan_hash'],'images':[{'target_id':row['target']['id'],**reviewed(row)}]}))
            promote=self.cli.promote
            def interrupted(*args):
                promote(*args)
                raise KeyboardInterrupt('simulated interruption after successful final copy')
            with patch.object(self.cli,'promote',side_effect=interrupted),self.assertRaises(KeyboardInterrupt):
                self.cli.apply_reviews(root/'manifest.json',path)
            output=root/'样张'/row['target']['filename']; original=output.read_bytes(); stamp=output.stat().st_mtime_ns
            done=self.cli.apply_reviews(root/'manifest.json',path)
            self.assertEqual(done['status'],'samples_ready')
            self.assertEqual(output.read_bytes(),original)
            self.assertEqual(output.stat().st_mtime_ns,stamp)

    def test_existing_different_output_is_never_overwritten(self):
        plan=require(self,'input_plan').build_plan(FIXTURE,types='home_banner')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'activity'; sample=self.cli.generate_batch(plan,root,sample_only=True,confirmed=True,adapter=SyntheticProvider())
            row=next(iter(sample['images'].values()))
            output=root/'样张'/row['target']['filename']; output.parent.mkdir(); output.write_bytes(b'existing unrelated content')
            with self.assertRaises(FileExistsError): self.review_pending(root,sample)
            self.assertEqual(output.read_bytes(),b'existing unrelated content')

    def test_interrupted_sample_promotion_resumes_without_overwrite_or_new_job(self):
        plan=require(self,'input_plan').build_plan(FIXTURE,types='home_banner')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'activity'; adapter=SyntheticProvider()
            sample=self.cli.generate_batch(plan,root,sample_only=True,confirmed=True,adapter=adapter)
            self.review_pending(root,sample)
            promote=self.cli.promote
            def interrupted(*args):
                promote(*args)
                raise KeyboardInterrupt('simulated interruption after approved sample promotion')
            with patch.object(self.cli,'promote',side_effect=interrupted),self.assertRaises(KeyboardInterrupt):
                self.cli.generate_batch(plan,root,confirmed=True,adapter=adapter)
            output=root/'产出图'/plan['targets'][0]['filename']; stamp=output.stat().st_mtime_ns
            done=self.cli.generate_batch(plan,root,confirmed=True,adapter=adapter)
            self.assertEqual(done['status'],'complete')
            self.assertEqual(adapter.submissions,1)
            self.assertEqual(output.stat().st_mtime_ns,stamp)
