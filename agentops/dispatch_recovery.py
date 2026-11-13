"""Recover coordinator claims that never acquired a worker handle."""


def recover_unstarted_jobs(ledger,run_id):
    recovered=[]
    for job in ledger.jobs(run_id):
        if job['state']=='interrupted' and not job.get('handle') and not job['stale'] and job['attempt']<3:
            ledger.retry(job['id'])
            ledger.set_review_task(job['id'],'qualification')
            recovered.append(job['number'])
    return recovered
