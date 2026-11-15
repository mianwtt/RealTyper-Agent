$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m realtyper_agent @args
