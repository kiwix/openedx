FROM python:3.8

# add zimwriterfs
ENV ZIMWRITERFS_VERSION 1.3.10-4
RUN wget https://download.openzim.org/release/zimwriterfs/zimwriterfs_linux-x86_64-${ZIMWRITERFS_VERSION}.tar.gz
RUN tar -C /usr/bin --strip-components 1 -xf zimwriterfs_linux-x86_64-${ZIMWRITERFS_VERSION}.tar.gz
RUN rm -f zimwriterfs_linux-x86_64-${ZIMWRITERFS_VERSION}.tar.gz
RUN chmod +x /usr/bin/zimwriterfs
RUN zimwriterfs --version

# Install necessary packages
RUN apt-get update -y \
    && apt-get install -y --no-install-recommends locales-all wget unzip ffmpeg libjpeg-dev libpng-dev jpegoptim pngquant gifsicle advancecomp \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /src/
RUN pip3 install -r /src/requirements.txt
COPY openedx2zim /src/openedx2zim
COPY setup.py *.md get_js_deps.sh MANIFEST.in /src/
RUN cd /src/ && python3 ./setup.py install

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
ENTRYPOINT ["entrypoint.sh"]

RUN mkdir -p /output
WORKDIR /output
CMD ["openedx2zim", "--help"]
