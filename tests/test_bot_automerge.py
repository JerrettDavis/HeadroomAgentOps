import json
import shutil
import subprocess
import unittest
from pathlib import Path

import yaml


class BotAutomergeTests(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("node"), "Node is required to execute the workflow script"
    )
    def test_protected_branch_uses_supported_graphql_variables_and_head_guard(self):
        workflow = yaml.safe_load(
            Path(".github/workflows/bot-automerge.yml").read_text()
        )
        script = workflow["jobs"]["enable"]["steps"][0]["with"]["script"]
        harness = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const name = 'JerrettDavis/HeadroomAgentOps';
const sha = 'a'.repeat(40);
const pr = {number:1,node_id:'PR_test',state:'open',draft:false,user:{type:'Bot'},
  head:{sha,repo:{full_name:name}},base:{ref:'main',repo:{full_name:name}}};
const checks = async () => [{status:'completed',conclusion:'success'}];
const runs = async () => ['ci.yml','security.yml'].map((path,i) => ({
  id:i+1,workflow_id:i+1,event:'pull_request',path:'.github/workflows/'+path,
  status:'completed',conclusion:'success'}));
let calls = 0;
const github = {rest:{repos:{get:async()=>({data:{owner:{type:'User'},fork:false,
  archived:false,default_branch:'main',allow_squash_merge:true}}),
  getBranch:async()=>({data:{protected:true}}),listCommitStatusesForRef:async()=>[]},
  pulls:{get:async()=>({data:pr})},checks:{listForRef:checks},
  actions:{listWorkflowRunsForRepo:runs}},paginate:async(fn)=>fn(),
  graphql:async(query,variables)=>{
    assert.ok(!Object.hasOwn(variables,'method'), 'Octokit reserves the method option');
    assert.equal(variables.mergeMethod,'SQUASH');
    assert.equal(variables.sha,sha);
    assert.match(query,/expectedHeadOid:\s*\$sha/);
    assert.match(query,/mergeMethod:\s*\$mergeMethod/);
    calls++;
  }};
const context = {repo:{owner:'JerrettDavis',repo:'HeadroomAgentOps'},
  eventName:'pull_request_target',payload:{pull_request:{number:1}}};
vm.runInNewContext('(async()=>{'+SCRIPT+'})()', {github,context,core:{info:()=>{}}})
  .then(()=>assert.equal(calls,1)).catch(error=>{console.error(error);process.exitCode=1;});
"""
        harness = "const SCRIPT = " + json.dumps(script) + ";\n" + harness
        result = subprocess.run(
            ["node", "-e", harness], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
