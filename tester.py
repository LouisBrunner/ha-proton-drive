# ruff: noqa: T201, D100, D103

import json
import os
from getpass import getpass
from pathlib import Path

from proton.proton import Credentials, Login, NewClient

cred_file_path = Path("credentials.json")


def update_creds(creds: Credentials) -> None:
    print("Updating credentials", creds)
    with cred_file_path.open("w") as file:
        file.write(
            json.dumps(
                {
                    "UID": creds.UID,
                    "AccessToken": creds.AccessToken,
                    "RefreshToken": creds.RefreshToken,
                    "SaltedKeyPass": creds.SaltedKeyPass,
                }
            )
        )


if cred_file_path.exists():
    print("Restoring credentials")
    with cred_file_path.open("r") as file:
        data = json.loads(file.read())
        creds = Credentials(
            UID=data["UID"],
            AccessToken=data["AccessToken"],
            RefreshToken=data["RefreshToken"],
            SaltedKeyPass=data["SaltedKeyPass"],
        )
else:
    print("Login")
    creds = Login(os.getenv("PROTON_EMAIL", ""), getpass(), "")
    update_creds(creds)


print("Creating client")
client = NewClient(
    creds,
    update_creds,
)
selected_share = os.getenv("PROTON_SHARE_ID")
if selected_share:
    client.SelectShare(selected_share)
    print("Creating root folder (none)")
    folder = client.MakeRootFolder("")
else:
    print("List shares")
    print(list(client.ListShares()))
    print("Creating root folder")
    folder = client.MakeRootFolder("Test/ABC")

print("files:", list(folder.ListFilesMetadata("123")))
print("Uploading one file")
folder.Upload("123", "def", "SomeName.tar", "{}", "requirements.txt")
try:
    print("Uploading another file (should fail)")
    folder.Upload("123", "def", "SomeName.tar", "{}", "README.md")
    raise ValueError
except RuntimeError as err:
    print("good", err)
print("files:", list(folder.ListFilesMetadata("123")))

link_id = folder.FindBackup("123", "def")
print("backup: ", link_id)
try:
    print("looking for non existing backup")
    folder.FindBackup("123", "def2")
    raise ValueError
except RuntimeError as err:
    print("good", err)

print("Downloading file")
path = client.DownloadFile(link_id)
print("downloaded", path)

input("Delete?")

print("Deleting file")
path = client.DeleteFile(link_id)
try:
    print("Deleting non existing file")
    path = client.DeleteFile(link_id)
    raise ValueError
except RuntimeError as err:
    print("good", err)
