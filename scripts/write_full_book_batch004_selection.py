"""Write the immutable authoritative selection for full-book Batch 004."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(r"D:\translate");RUN=ROOT/'runs'/'full-book-20260916T000200Z-decb09';SCOPE=RUN/'full_book_translation_scope.json';OUT=RUN/'full_book_batch_004_selection.json'
def ids_from(path,key):
 return json.loads(path.read_text(encoding='utf8'))[key]
def main():
 if OUT.exists():raise RuntimeError('IMMUTABLE_SELECTION_ALREADY_EXISTS')
 scope=json.loads(SCOPE.read_text(encoding='utf8')); recovery=json.loads((RUN/'full_book_recovery_sweep_002'/'recovery_sweep_closure.json').read_text(encoding='utf8'))
 if recovery['unresolved_count']!=0 or recovery['final_approved_count']!=3:raise RuntimeError('RECOVERY_SWEEP_002_NOT_CLOSED')
 completed=set(ids_from(RUN/'full_book_batch_001_selection.json','selected_logical_work_order_ids'));completed.update(item['logical_work_order_id'] for item in json.loads((RUN/'full_book_batch_002_selection.json').read_text(encoding='utf8'))['selected_work_orders']);completed.update(ids_from(RUN/'full_book_batch_003_selection.json','selected_logical_work_order_ids'))
 eligible=[item for item in scope['logical_work_orders'] if item['release_status']=='PENDING_FIRST_PASS' and item['logical_work_order_id'] not in completed and item['phase']!='toc_late_stage'];selected=eligible[:150];selected_ids=[item['logical_work_order_id'] for item in selected]
 if len(selected_ids)!=150 or len(set(selected_ids))!=150 or selected_ids[0]!='body.p.01369':raise RuntimeError('AUTHORITATIVE_BATCH_004_SELECTION_MISMATCH')
 artifact={'artifact_type':'FULL_BOOK_BATCH_SELECTION','batch':'FULL_BOOK_BATCH_004','macro_target':150,'micro_checkpoint_size':25,'authoritative_scope':str(SCOPE),'authoritative_scope_sha256':hashlib.sha256(SCOPE.read_bytes()).hexdigest(),'source_docx':scope['source_docx'],'source_sha256':scope['source_sha256'],'current_release_recovery_closure':str(RUN/'full_book_recovery_sweep_002'/'recovery_sweep_closure.json'),'chapter_3_footnote_evidence':str(RUN/'chapter_3_verified_footnote_evidence_v1.json'),'selection_order':'authoritative remaining-first-pass source order after completed Batches 001–003 and closed recovery sweeps; TOC is explicit late-stage content','selected_logical_work_order_ids':selected_ids,'selected_orders':[{key:item.get(key) for key in ('logical_work_order_id','physical_member_display_ids','composite_group_id','phase','release_status')} for item in selected],'selected_id_set_sha256':hashlib.sha256('\n'.join(selected_ids).encode('utf8')).hexdigest(),'selection_validation':{'selected_logical_count':150,'duplicate_logical_count':0,'previous_batch_overlap_count':len(set(selected_ids)&completed),'toc_late_stage_count':sum(item['phase']=='toc_late_stage' for item in selected),'phase_counts':{phase:sum(item['phase']==phase for item in selected) for phase in sorted({item['phase'] for item in selected})}}}
 with OUT.open('x',encoding='utf8',newline='\n') as f:json.dump(artifact,f,ensure_ascii=False,indent=2);f.write('\n')
 print(selected_ids[0],selected_ids[-1],artifact['selection_validation'])
if __name__=='__main__':main()
