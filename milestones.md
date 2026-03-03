# Milestones
1. Definir escopo MVP, seeds (channelIds/playlists/queries) e limite diário de cota.
2. Configurar credenciais (API key/OAuth opcional) e variáveis de ambiente.
3. Implementar cliente YouTube Data API v3 com `part`/`fields` e retry/backoff.
4. Implementar fluxo: `channels.list` → uploads playlist → `playlistItems.list` → `videos.list`.
5. Implementar paginação com `pageToken` e `maxResults`.
6. Persistência: esquema para canais, vídeos, execuções; dedup por `id`.
7. Logging/rastreabilidade: parâmetros, timestamps, quota estimada por chamada.
8. (Opcional) Comentários: `commentThreads.list` + `comments.list`.
9. Scripts de execução incremental e exportação de dados.
10. Testes mínimos e documentação de uso.
11. Criar comandos e scripts simplificado para consulta de cotas.
12. Criar logs de monitoramento humano da aplicação  
13. Incluir logs no arquivo de collection_flow.py
14. Create methods to collect and upsert comments in the database
15. Crie uma pipeline de execução diferente para cada caso de uso da pipeline. Considere os casos principais: buscas por canais do youtube; buscas por vídeos no youtube; busca por vídeos no canal; buscas por comentários e threads de comentários; buscas de dados de usuários e ou canais;
16. Atualize o README.md e crie uma seção de docs para o repositório.
17. Faça o projeto amigável para a execução personalizada em jupyternotebooks. Você pode criar alguns exemplos de jupyteer notebook com uso funcional.
18. Atualize o README.md e revise a documentação completa do repositório
19. Crie um diagrama mermaid para representar este projeto