# Programas e sistema

Área de diagnóstico e manutenção por instalação. O objetivo é explicar onde o
espaço está, distinguir dados necessários de candidatos à manutenção e usar o
procedimento do proprietário dos dados para as ações disponíveis.

## Navegação e medição

Abra **Programas e sistema** na navegação lateral. **Detectar instalações**
consulta serviços locais e diretórios usuais do MySQL, sem iniciar serviços.
Windows aparece como uma instalação gerenciada. Selecione uma instalação e
clique **Analisar instalação**; o botão superior **Analisar diretórios** também
atua sobre a instalação selecionada enquanto esta área estiver aberta.

A tela informa caminho, situação do serviço, total inventariado, tipos de
conteúdo e sugestões. Duplo clique em um tipo mostra os cem maiores arquivos
do grupo. A medição usa metadados e não abre conteúdo de bancos ou documentos.
Comparações entre medições da mesma sessão ficam no relatório em memória.

> [!IMPORTANT]
> Tamanho ocupado não é espaço recuperável. Arquivos compactados, esparsos,
> compartilhados e dados válidos podem tornar essas grandezas diferentes.
> O total é lógico e evita duplicar hardlinks; erros de acesso e arquivos
> offline/links pulados aparecem nos avisos. Cancelamento deixa resultado parcial.

## MySQL

Uma instalação é descoberta por serviço (`mysqld`), por diretórios `mysql*` na
unidade do sistema ou pelas instalações em Program Files/MySQL. Instâncias que
compartilham executáveis podem aparecer separadas por serviço. Seus totais não
devem ser somados como se todos os arquivos fossem exclusivos.

O diretório de dados é inferido do `--defaults-file`/`my.ini`, com fallback para
`data` na instalação. São lidos somente parâmetros de localização e porta de
seções de servidor; contas e senhas de configurações não são retornadas. Outras
fontes de configuração e caminhos personalizados de logs podem alterar o escopo;
a localização efetiva precisa ser confirmada pela consulta SQL.

Tipos de conteúdo:

- Tabelas e índices: `.ibd`, `.MYD`, `.MYI`.
- Logs binários e logs de réplica identificados por nomes conhecidos.
- Logs de diagnóstico, exportações/backups a revisar e executáveis/bibliotecas.
- Arquivos internos de recuperação, configuração e demais conteúdos preservados.

Não se deduz que uma tabela é descartável pelo nome. Nomes que sugerem histórico
geram apenas uma sugestão para investigar sua política de retenção.

### Consulta do servidor

**Conectar / consultar MySQL** pede usuário, senha e porta. A conexão é restrita
ao computador local (`127.0.0.1`); usa Connector/Python já disponível neste
computador. A senha não vai para argumentos de processo, preferências ou
relatórios. Credenciais ficam somente em memória até trocar de instalação,
detectar novamente ou fechar o aplicativo. Cada tarefa abre e fecha sua conexão.

A primeira consulta compara o `datadir` real com o selecionado. Se a porta
responder por outra instância, a operação é recusada antes de consultar tabelas
ou oferecer manutenção. Isso importa, por exemplo, quando dois serviços possuem
a mesma porta na configuração e apenas um está em execução.

As consultas seguintes obtêm versão, UUID, binlogs, arquivo ativo, opções de
retenção, status de replicação e estimativas das cinquenta maiores tabelas visíveis à conta.
Não são consultados os registros das tabelas. O dialeto de SHOW considera as
diferenças entre MySQL 8.0 e versões 8.4/9.x. Incompatibilidade ou privilégios
insuficientes deixam o diagnóstico parcial e bloqueiam o expurgo quando afetam
sua verificação. Não é oferecido SQL arbitrário, DELETE, TRUNCATE ou OPTIMIZE.

### Expurgo de binlogs

O único comando SQL mutável oferecido é `PURGE BINARY LOGS TO`, parametrizado.
O usuário escolhe o primeiro arquivo que será **mantido**; os anteriores são
candidatos. O arquivo ativo nunca pode pertencer à seleção a remover.

São obrigatórios:

1. Consulta bem-sucedida da instância, arquivo ativo e replicação.
2. Ausência de réplicas/canais identificados. Esta versão não automatiza o
   planejamento de uma topologia de replicação existente.
3. Confirmação explícita de que os backups não dependem do intervalo removido.
4. Confirmação explícita de que nenhuma réplica, inclusive desconectada, depende
   dele. Uma lista vazia de SHOW não comprova isso por conta própria.
5. Revisão final da instância, corte, quantidade de arquivos e volume.

Imediatamente antes do expurgo, o programa reconfirma UUID, datadir, lista e
tamanhos dos arquivos anteriores ao corte, arquivo ativo e replicação. Mudanças
no intervalo interrompem a operação. Cancelamento antes da execução do comando
impede o expurgo; depois de iniciado, aguarda-se o servidor terminar.

> [!CAUTION]
> Expurgo é permanente e depende da informação do responsável sobre backups e
> réplicas desconectadas. Nenhum arquivo de banco é removido pelo sistema de
> arquivos. A confirmação não elimina a necessidade de uma política de retenção.

Após o comando, a lista é consultada novamente para informar quantos arquivos
realmente desapareceram; o volume indicado não é promessa de espaço físico.

## Windows

O inventário inclui a pasta real do Windows, os arquivos gerenciados de memória
virtual/hibernação na raiz e Package Cache. Hardlinks são deduplicados. A divisão
por pastas não substitui a análise oficial de WinSxS, que pode compartilhar
conteúdo com outras partes do sistema.

Operações disponíveis:

| Operação | Procedimento e consequência |
|---|---|
| Analisar componentes | DISM `/Online /Cleanup-Image /AnalyzeComponentStore /English` |
| Limpar componentes substituídos | DISM `/Online /Cleanup-Image /StartComponentCleanup /English`, após confirmação |
| Desativar hibernação | `powercfg /hibernate off`, após confirmação da perda de hibernação, Inicialização Rápida e suspensão híbrida |
| Armazenamento Windows | Abre a página nativa para revisão pelo usuário |
| Limpeza de Disco | Abre a ferramenta nativa sem seleção ou execução automática |

DISM e powercfg exigem administrador. **Abrir como administrador** solicita UAC
e abre outra janela em Programas e sistema; não inicia uma limpeza. A janela
anterior permanece aberta para preservar relatórios e seleções não salvos.

Não há `/ResetBase`, alteração do arquivo de paginação, remoção de drivers,
exclusão manual de WinSxS/Installer ou reinicialização automática. Para restaurar
a hibernação posteriormente, o administrador pode usar `powercfg /hibernate on`.

A saída nativa aparece no progresso e fica em memória. Processos de manutenção
iniciados não são mortos pelo cancelamento; aguarda-se sua conclusão. Código
3010 informa necessidade de reiniciar, sem fazê-lo automaticamente. O Windows
continua responsável pelos seus registros nativos de manutenção; o Destralha
não cria arquivos de log ou saída temporários.

## Relatórios e manutenção do código

Descoberta, inventário, consultas e resultados ficam no relatório da sessão.
**Salvar relatório** é a única operação que grava esse relatório. Credenciais
não são serializadas. A nova área não altera o catálogo de exclusão de caches
nem as marcações das outras categorias.

- `maintenance/inventory.py`: escopo, metadados, classificação e hardlinks.
- `maintenance/mysql.py`: descoberta, consulta SQL e contrato do expurgo.
- `maintenance/windows.py`: comandos fixos e ferramentas nativas do sistema.
- `maintenance/view.py`: revisão, confirmações e integração por fila de eventos.
- `tests/test_maintenance.py`: contratos com arquivos artificiais, SQL e processos
  simulados; os testes não executam manutenção real do Windows ou dos bancos.

Referências: [expurgo MySQL](https://dev.mysql.com/doc/refman/8.0/en/purge-binary-logs.html),
[limpeza WinSxS](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/clean-up-the-winsxs-folder),
[Windows Installer](https://learn.microsoft.com/en-us/troubleshoot/windows-client/application-management/missing-windows-installer-cache),
[hibernação](https://learn.microsoft.com/en-us/windows/troubleshoot/windows-client/setup-upgrade-and-drivers/disable-and-re-enable-hibernation).
