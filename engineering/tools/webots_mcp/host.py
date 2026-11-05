"""Read-only S04 report/log bridge. The DSH host authenticates the caller context."""
import argparse
import json
import os
from pathlib import Path
from .evidence import EvidenceStore

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind',choices=('report','evidence','attribute'))
    parser.add_argument('--task-id',required=True)
    parser.add_argument('--room-id',required=True)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--report-type',choices=('model_manifest','conversion_report','experiment_report'))
    parser.add_argument('--issue-id')
    parser.add_argument('--name',default='worker')
    parser.add_argument('--offset',type=int,default=0)
    parser.add_argument('--limit',type=int,default=16384)
    parser.add_argument('--expected-sha256')
    args=parser.parse_args()
    declared=os.environ.get('DSH_STATE_DIR','')
    if not declared or not Path(declared).is_absolute() or not Path(declared).is_dir():
        parser.error('DSH_STATE_DIR must explicitly name an existing absolute directory')
    from _store import state_dir
    resolved=Path(state_dir()).resolve(strict=True)
    if resolved != Path(declared).resolve(strict=True): parser.error('state_dir mismatch')
    try:
        store=EvidenceStore(resolved,task_id=args.task_id,room_id=args.room_id)
        if args.kind=='report':
            data=store.read_report(args.run_id,args.report_type)
        elif args.kind=='attribute':
            from .repair import attribute
            from .evidence import _identifier
            data=attribute(store,args.run_id,_identifier(args.issue_id))
        else:
            if not 4 <= args.limit <= 16384: raise ValueError('Host page limit must be 4..16384')
            data=store.read_log(args.run_id,name=args.name,offset=args.offset,limit=args.limit,expected_sha256=args.expected_sha256)
        result={'ok':True,'data':data}
    except PermissionError: result={'ok':False,'error_code':'OWNERSHIP_MISMATCH'}
    except FileNotFoundError: result={'ok':False,'error_code':'EVIDENCE_NOT_FOUND'}
    except (ValueError,TypeError,KeyError,AttributeError) as error: result={'ok':False,'error_code':'EVIDENCE_INVALID','hint':str(error)[:256]}
    except OSError as error: result={'ok':False,'error_code':'EVIDENCE_IO_ERROR','hint':str(error)[:256]}
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
if __name__=='__main__': main()

