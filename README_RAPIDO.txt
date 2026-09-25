QUALIFX ERP API - Passos rapidos

1) Copie esta pasta para C:\QualifXApi
2) Renomeie .env.example para .env
3) Edite o .env e informe:
   - SQL_PASSWORD
   - LOVABLE_DB_KEY completa, service_role legacy ou secret key completa
   - FINAL_SERVER_BASE_PATH correto
4) Instale dependencias:
   powershell -ExecutionPolicy Bypass -File instalar_dependencias.ps1
5) Rode a API:
   powershell -ExecutionPolicy Bypass -File rodar_api.ps1
6) Em outro PowerShell, rode ngrok:
   powershell -ExecutionPolicy Bypass -File rodar_ngrok.ps1
7) Teste:
   https://certificado.lotes.ngrok.app/health
   https://certificado.lotes.ngrok.app/docs
8) No Lovable:
   VITE_API_URL=https://certificado.lotes.ngrok.app

Atencao: nao publique .env, service_role, secret key ou senha SQL no GitHub.
