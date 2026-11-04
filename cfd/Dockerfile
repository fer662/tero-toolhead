# OpenFOAM 2412, pinned multi-architecture image (AMD64 and ARM64).
FROM microfluidica/openfoam@sha256:f5fd9a04a236c0db5717a4d5eeeb5bd35fb498e7ca7a347373b2ea4a8f4c901c
USER root
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       python3-numpy python3-vtk9 python3-matplotlib \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work/case
ENTRYPOINT ["/bin/bash"]
