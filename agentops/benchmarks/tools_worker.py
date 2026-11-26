"""Executed by the isolated pinned Inspect environment; no provider credentials."""
import json
import os
from pathlib import Path
import sys

def main():
    if sys.argv[1] == 'aiperf':
        from aiperf.cli import app
        # Invoke the genuine CLI parser in-process: the dummy loopback key never
        # appears in the OS command line or leaves this harness environment.
        app([*sys.argv[2:], '--api-key', os.environ['STUDIO_API_KEY']])
        return
    from inspect_ai import Task, eval as inspect_eval
    from inspect_ai.dataset import MemoryDataset, Sample
    from inspect_ai.model import ChatMessageAssistant, ChatMessageSystem, ChatMessageUser
    from inspect_ai.solver import generate
    task = json.loads(Path(sys.argv[1]).read_text('utf-8'))
    messages = {'user': ChatMessageUser, 'system': ChatMessageSystem, 'assistant': ChatMessageAssistant}
    dataset = MemoryDataset([Sample(id=str(task['id']), input=[messages[m['role']](content=m['content']) for m in task['messages']])])
    result = inspect_eval(Task(name='studio-paired-attempt', dataset=dataset, solver=generate()),
                  model='openai-api/studio/studio-model',
                  temperature=0, max_tokens=task.get('max_tokens', 1024), max_retries=0,
                  log_dir=str(Path(sys.argv[2]) / 'native'), display='none', score=False)
    if len(result) != 1 or result[0].status != 'success':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
