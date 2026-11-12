"""Run the GUI's managed-node installer with the real release archives."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile


CHECKSUMS = {
    "x86_64-linux-gnu.tar.gz": (
        "b80d9c3e04da78fb6f0569685673418cf686fadba9042d926d13fb87ff503f9e",
        "61545c17c3120fb7412f896d248b11993af04ec8817aad3649c55b2afb044963",
    ),
    "arm64-apple-darwin.tar.gz": (
        "16a097c09fbd7eb78b240ce1dae123663ea2e5e377cfd6a951e71e227e23cf2f",
        "cd3dcdacb4ff861e862b2d7df4786ecef66472e2468dca1673a70dd761e064e7",
    ),
    "x86_64-apple-darwin.tar.gz": (
        "bc506958d0f387c1ea770bdc7c7192a505fa645ff62cabcc7761fa7eb89e867e",
        "53b1e6ffd92843ca82ef4a737d7a53cb0a9fab15816a417a41f12fc954071447",
    ),
    "win64.zip": (
        "c99ef173471c58e6766d9eebd12e6c35349082eeed3939bc99eed58ef57db587",
        "b5b0c2cd007d69e96880907c79f9ae19ae82728f95fc8c923b4a07dab795b45d",
    ),
}


def verify_archive(archive, checksum):
    with archive.open("rb") as data:
        if hashlib.file_digest(data, "sha256").hexdigest() != checksum:
            raise ValueError(f"Incorrect checksum for {archive.name}")


def download(directory, version, flavor, checksum):
    archive = directory / f"bitcoin-{version}-{flavor}"
    url = f"https://bitcoincore.org/bin/bitcoin-core-{version}/{archive.name}"
    with urllib.request.urlopen(url, timeout=60) as source, archive.open("wb") as data:
        shutil.copyfileobj(source, data)
    verify_archive(archive, checksum)
    return archive


def extract_executable(archive, version, name, destination):
    if archive.suffix == ".zip":
        name += ".exe"
        with zipfile.ZipFile(archive) as package:
            contents = package.read(f"bitcoin-{version}/bin/{name}")
    else:
        with tarfile.open(archive, "r:gz") as package:
            with package.extractfile(f"bitcoin-{version}/bin/{name}") as executable:
                contents = executable.read()
    destination = destination / name
    destination.write_bytes(contents)
    destination.chmod(0o755)
    return destination


def run_test(previous, current, directory):
    source = Path("liana-gui/src/node/bitcoind.rs")
    original = source.read_bytes()
    newline = b"\r\n" if b"\r\n" in original else b"\n"
    marker = newline.join((b"#[cfg(test)]", b"pub mod tests {", b""))
    if original.count(marker) != 1:
        raise RuntimeError("Could not find the node test module")
    modified = original.replace(
        marker,
        marker + b'    mod managed_core_qa { include!(env!("LIANA_QA_SOURCE")); }' + newline,
    )
    env = {
        **os.environ,
        "LIANA_QA_SOURCE": str(Path(__file__).with_name("managed-core.rs").resolve()),
        "LIANA_QA_CORE_ARCHIVE": str(current.resolve()),
        "LIANA_QA_PREVIOUS_CORE": str(
            extract_executable(previous, "31.1", "bitcoind", directory).resolve()
        ),
        "LIANA_QA_BITCOIN_CLI": str(
            extract_executable(current, "32.1", "bitcoin-cli", directory).resolve()
        ),
    }
    cargo = ["cargo", "test", "--locked", "-p", "liana-gui", "--lib"]
    if os.name == "nt":
        cargo.append("--no-default-features")
    source.write_bytes(modified)
    try:
        subprocess.run(
            [*cargo, "qa_upgrades_a_real_managed_regtest_node", "--", "--nocapture"],
            env=env,
            check=True,
        )
    finally:
        source.write_bytes(original)


def main():
    if len(sys.argv) not in (2, 4):
        raise SystemExit("Usage: run-managed-core.py FLAVOR [OLD_ARCHIVE NEW_ARCHIVE]")
    flavor = sys.argv[1]
    previous_hash, current_hash = CHECKSUMS[flavor]
    with tempfile.TemporaryDirectory(prefix="managed-core-qa-") as temporary:
        directory = Path(temporary)
        if len(sys.argv) == 4:
            previous, current = map(Path, sys.argv[2:])
            verify_archive(previous, previous_hash)
            verify_archive(current, current_hash)
        else:
            previous = download(directory, "31.1", flavor, previous_hash)
            current = download(directory, "32.1", flavor, current_hash)
        run_test(previous, current, directory)


if __name__ == "__main__":
    main()
