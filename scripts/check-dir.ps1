$env:AGENT_BROWSER_SOCKET_DIR = "C:\Users\x\AppData\Local\agent-browser"
New-Item -Path $env:AGENT_BROWSER_SOCKET_DIR -ItemType Directory -Force | Out-Null
Get-ChildItem -Path $env:AGENT_BROWSER_SOCKET_DIR -Force | Format-List Name
Write-Host "SOCKET_DIR=$env:AGENT_BROWSER_SOCKET_DIR"
Write-Host "---DONE---"
