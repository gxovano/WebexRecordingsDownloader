import requests
import json
import csv
import time
import re
import os
import datetime

from dotenv import load_dotenv
from pathlib import Path
dotenv_path = Path('.env')
load_dotenv(dotenv_path=dotenv_path)

DOWNLOAD_DIR = "Downloaded-Recordings/"

def buildRecordingFileName(topic, timeRecorded, originalFileName, recordingId):
    _, ext = os.path.splitext(originalFileName)
    if not ext:
        ext = '.mp4'

    if not topic and not timeRecorded:
        return originalFileName

    safeTopic = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', topic or 'recording')
    safeTopic = re.sub(r'\s+', ' ', safeTopic).strip()[:120] or 'recording'

    timestamp = ''
    if timeRecorded:
        try:
            dt = datetime.datetime.fromisoformat(timeRecorded.replace('Z', '+00:00'))
            timestamp = dt.strftime('%Y-%m-%d_%H-%M-%S')
        except ValueError:
            timestamp = re.sub(r'[<>:"/\\|?*]', '', timeRecorded)[:19]

    parts = [safeTopic]
    if timestamp:
        parts.append(timestamp)
    baseName = '_'.join(parts)
    fileName = baseName + ext

    targetPath = os.path.join(DOWNLOAD_DIR, fileName)
    if os.path.exists(targetPath):
        fileName = f"{baseName}_{recordingId[:8]}{ext}"

    return fileName

def getDownloadLinks(headers):
    recordingDownloadLink = None
    with open ('recordings.csv', 'r') as csvfile:
        recs = csv.reader(csvfile)
        for row in recs:
            if not row or row[0] == 'recordingId':
                continue
            id = row[0]
            hostEmail = row[1].replace('@','%40').replace("+", "%2B")
            topic = row[2] if len(row) > 2 else ''
            timeRecorded = row[3] if len(row) > 3 else ''
            print("RecordingId: "+id+", HostEmail: "+hostEmail)
            url = 'https://webexapis.com/v1/recordings/'+id+'?hostEmail='+hostEmail
            #print(url)
            result = requests.get(url, headers=headers)
            downloadLink = json.loads(result.text)
            if not topic:
                topic = downloadLink.get('topic', '')
            if not timeRecorded:
                timeRecorded = downloadLink.get('timeRecorded', '')
            links = downloadLink['temporaryDirectDownloadLinks']
            recordingDownloadLink = links['recordingDownloadLink']
            print("Download Link: "+recordingDownloadLink)
            if recordingDownloadLink is not None:
                try:
                    recording = requests.get(recordingDownloadLink)
                    if recording.status_code == 200:
                        fileName = recording.headers.get('Content-Disposition').split("''")[1]
                        saveAs = buildRecordingFileName(topic, timeRecorded, fileName, id)
                        print("Filename: "+str(saveAs))
                        with open(DOWNLOAD_DIR+saveAs, 'wb') as file:
                            file.write(recording.content)
                            print(saveAs+" saved!")
                    elif recording.status_code == 429:
                        retry_after = recording.headers.get("retry-after") or recording.headers.get("Retry-After")
                        print("Rate limited. Waiting "+str(retry_after)+" seconds.")
                        time.sleep(int(retry_after))
                    else:
                        print("Unable to download, something went wrong!")
                        print("Status Code: "+str(recording.status_code))
                except Exception as e:
                    print(e)        
            else:
                print("something went wrong.")