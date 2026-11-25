# Disposable SSH transport validation only; never a production worker image.
FROM headroom-benchmark:validation
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server \
    && mkdir -p /run/sshd /root/.ssh \
    && ssh-keygen -A \
    && rm -rf /var/lib/apt/lists/*
EXPOSE 22
ENTRYPOINT ["/usr/sbin/sshd", "-D", "-e"]
