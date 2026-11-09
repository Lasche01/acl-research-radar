"""One-shot scheduled entry point; a failed fetch still produces an honest status report."""
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta
from aclbrief.service import Service


if __name__ == '__main__':
    data_dir = Path(os.environ.get('ACL_DATA_DIR', str(Path.home()/'ACLBriefingData')))
    service = Service(data_dir)
    result = service.collect()
    folder = data_dir / 'briefings'
    folder.mkdir(exist_ok=True)
    day = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
    destination = folder / (day + '.md')
    destination.write_text(service.briefing(), encoding='utf-8')
    print(destination)
    raise SystemExit(0 if result['status'] == 'completed' else 2)
