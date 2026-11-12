# Execution and publication boundaries

On Linux, run the coordinator as a non-root user with a non-root primary group.
Workers map that UID/GID so private bind-mounted job files remain writable
without granting world access. Docker Desktop retains the image's UID/GID 1000.

The coordinator retains GitHub credentials and owns every external write. Workers run as a nonroot user in disposable Linux containers with a read-only root, no capabilities, no-new-privileges, resource limits and an intrinsic time budget. Workers receive only their checkout, inputs, output directory and selected model authentication. They receive no host home, primary checkout, shared Git metadata, publisher credentials or Docker socket.

Model authentication remains accessible to the selected harness inside the container. Network access is available for inference and ordinary dependency retrieval. Treat PR content as untrusted; container isolation does not make arbitrary code trustworthy. Workflow and installer execution requires source review; unknown provenance remains a hold.

CLI packages and npm integrity hashes are locked. The base image is pinned by digest. OS packages installed during image construction are not a frozen package snapshot. Record the resolved image SHA for each session.

The host imports candidate bundles into a trusted bare repository with hooks, fsmonitor, replacement objects and inherited credential helpers disabled. Contributor history must remain an ancestor. The final pre-push check covers the live head, main, base, discussion, target ref/repository and open state. Leases protect the actual ref update. Protected workflow/tooling changes remain held pending additional candidate-source qualification.

Publication uses an OS lock, stable action IDs and a durable journal. Exact authenticated actor/body/type receipts reconcile comments and reviews. Lost-response writes remain ambiguous until reconciled; definite GitHub 4xx rejections are recorded separately. No merge endpoint exists. Owner self-approval is prohibited.

Evidence artifacts are hash verified. Candidate archives bind bundle bytes to their original job, session, head and main. Reuse also checks policy version, base, substantive discussion, source and notices. Edited feedback or changed author evidence invalidates reuse. Cleanup removes only resolved, owned paths after confirmed process termination and preserves diagnostics and output evidence.

## Native Copilot relay

Native OAuth is read privately from the installed Copilot credential store by an owned host provider process. It is never staged in worker mounts, model environment files or relay metadata. The loopback listener issues an independent random model capability and routes only to the fixed HTTPS Copilot service without redirects. Selected-model enforcement, bounded requests and handlers, stateless inline input, and client-side tools prevent repository and account-storage capabilities from crossing this boundary. The capability itself is sensitive and remains in ignored local state.

The relay accepts self-contained reasoning history and function/custom tool outputs; remote file URLs, item references, stored prompts, conversations, previous response IDs and provider tools are rejected. It records no request bodies or credentials. Fixed diagnostic codes and a bounded vocabulary of schema field names explain policy rejections without exposing content.

A heartbeat keeps the owned child alive during coordination. Coordinator loss allows one worker time budget plus 60 seconds of grace; explicit completion retires the relay and removes its ready capability. Restarted coordinators can adopt running worker handles without copying native OAuth. Windows atomic heartbeat writes retry brief local sharing violations with a fixed bound. Native relay state is local and must not be committed.
