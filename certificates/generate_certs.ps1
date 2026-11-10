# Intentar buscar la ruta de OpenSSL en las ubicaciones predeterminadas de Git
$OpenSSLPath = "C:\Program Files\Git\usr\bin\openssl.exe"
if (-not (Test-Path $OpenSSLPath)) {
    $OpenSSLPath = "C:\Program Files (x86)\Git\usr\bin\openssl.exe"
}
if (-not (Test-Path $OpenSSLPath)) {
    # Si no se encuentra, intentamos usar el alias por defecto por si acaso
    $OpenSSLPath = "openssl"
}

Write-Host "Usando OpenSSL desde: $OpenSSLPath" -ForegroundColor Cyan

# Ejecución de comandos
& $OpenSSLPath genrsa -out ca.key 4096
& $OpenSSLPath req -x509 -new -nodes -key ca.key -sha256 -days 365 -out ca.crt -subj "/C=MX/ST=Edomex/L=Ecatepec/O=MiniDFS/CN=MiniDFS-RootCA"

& $OpenSSLPath genrsa -out central.key 2048
& $OpenSSLPath req -new -key central.key -out central.csr -subj "/C=MX/ST=Edomex/L=Ecatepec/O=MiniDFS/CN=localhost"
& $OpenSSLPath x509 -req -in central.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out central.crt -days 365 -sha256

& $OpenSSLPath genrsa -out datanode.key 2048
& $OpenSSLPath req -new -key datanode.key -out datanode.csr -subj "/C=MX/ST=Edomex/L=Ecatepec/O=MiniDFS/CN=localhost"
& $OpenSSLPath x509 -req -in datanode.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out datanode.crt -days 365 -sha256

# Limpieza de temporales
Remove-Item *.csr -ErrorAction SilentlyContinue
Remove-Item *.srl -ErrorAction SilentlyContinue

# Listar de forma segura en cualquier versión de PowerShell
Get-ChildItem | Where-Object { $_.Extension -eq ".crt" -or $_.Extension -eq ".key" } | Select-Object Name
