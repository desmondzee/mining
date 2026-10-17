ARG ISAAC_TAG=6.2.0
FROM nvcr.io/nvidia/isaac-sim:${ISAAC_TAG}

ENV ACCEPT_EULA=Y \
    PRIVACY_CONSENT=Y \
    NVIDIA_DRIVER_CAPABILITIES=all \
    PYTHONUNBUFFERED=1

WORKDIR /mining
ENTRYPOINT ["/isaac-sim/python.sh"]
CMD ["/mining/isaac/sim/main.py", "--brain", "127.0.0.1:9000", "--world", "/data/worlds/quarry", "--cache", "/cache"]
