# Destralha

Aplicativo local em Python/Tkinter para medir e selecionar os diretórios já
analisados durante a limpeza. Usa as pastas da conta que estiver executando o
programa. A leitura funciona sem elevação;
alguns arquivos protegidos podem exigir execução como administrador.

## Abrir e utilizar

A janela Destralha tem navegação lateral para três categorias, com filtros e
seleções independentes. Logo, ícone e cores verde-petróleo/turquesa seguem a
identidade aprovada. Não há abas aninhadas.

- **Caches**: todos os itens já classificados como cache no catálogo, incluindo
  Chrome, Edge, pip/Python, npm, aplicativos e ferramentas. Não inclui pacotes
  instalados, ambientes virtuais, modelos ou mídia offline.
- **Downloads**: revisão de arquivos por idade ou tamanho, com regras próprias.
  A função agora fica integrada à janela principal, sem abrir outra janela.
- **Outros**: reúne os controles para modelos, mídia offline,
  instaladores, diagnósticos e diretórios preservados. Caches não ficam duplicados.

A categoria Caches é determinada por `risk=cache` e ausência de proteção no
catálogo, após revisão do contrato de descarte. A classificação não remove
guardas de processo, arquivo em uso ou conteúdo desconhecido.
As preferências de Caches ficam em `cache-preferences.json`; Outros mantém
`preferences.json`. As marcações de uma categoria não entram na limpeza de outra.

### Bloqueios do pip

Python, pythonw, py ou WebView abertos por outro motivo não bloqueiam mais a
limpeza do pip. Chamadas identificáveis ao pip, como `python -m pip`, `py -m pip`,
`pip.exe` e o ponto de entrada `pip/__main__.py`, ainda bloqueiam temporariamente
a limpeza, assim como instaladores e processos que citam o caminho do cache.
Isso evita interromper operações sobre o cache sem exigir fechar todo Python.

Uso do pip por código dentro de outro aplicativo pode não aparecer na linha de
comando. Por isso, as verificações exclusivas de arquivos continuam obrigatórias:
se um arquivo estiver em uso, o alvo é preservado. Não se encerram processos.
Em Outros, as guardas específicas de cada aplicativo permanecem inalteradas.

### Executar uma revisão

No computador em que foi preparado, abra `C:\X\Destralha\Destralha.lnk`.
Também é possível abrir `Abrir Destralha.pyw`. Este é o único inicializador;
o projeto mantém apenas o atalho **Destralha**.
É necessário Python 3.10 ou mais recente com Tkinter, disponível no instalador
Python para Windows, e Pillow para carregar a marca e os ícones em memória.
Pillow 12.1.0 já está instalado neste computador; nenhuma nova biblioteca foi
instalada durante a reformulação. Em outro computador, `requirements.txt`
registra a versão validada. Não é necessário framework adicional de interface.

1. Clique em **Analisar diretórios** no topo. Uma única ação atualiza Caches,
   Downloads e Outros. Downloads usa o prazo definido em sua categoria; Outros respeita
   a opção de incluir diretórios preservados. Nenhum conteúdo é excluído.
2. Expanda um aplicativo para consultar seus diretórios e as condições de limpeza.
3. Marque as caixas na coluna **Limpar**. Em Caches, **Marcar Tudo** seleciona
   os alvos selecionáveis visíveis após busca e filtros, inclusive os que aguardam
   liberação. Não marca itens protegidos ou de formato desconhecido.
   **Desmarcar Tudo** limpa toda a seleção dessa categoria, inclusive os itens ocultos.
4. Use **Simular** para revisar caminhos e bloqueios atuais sem excluir.
5. **Revisar limpeza** abre a lista concreta dos alvos, inclusive os ocultos
   pelos filtros, seus efeitos e impedimentos. Somente **Confirmar exclusão**
   nessa janela inicia a remoção.
6. A coluna **Detalhes** (ou Enter sobre uma linha) mostra o caminho completo,
   efeito, data/motivo em Downloads e condições atuais. Ctrl+F focaliza a busca.

### Progresso e resultado

Análise e limpeza abrem uma janela com etapa atual, alvo em execução e controle
de cancelamento. **Continuar em segundo plano** recolhe essa janela; uma faixa
de status permanece visível na aplicação, com **Ver progresso** para reabri-la.
Percentuais se referem à quantidade conhecida de alvos na etapa atual. A busca
em Downloads usa indicador indeterminado enquanto não houver total conhecido;
não se inventam estimativas de tempo ou percentuais globais.

**Relatório** abre o resultado em memória da última análise, simulação ou limpeza,
inclusive avisos, preservações e itens não processados. **Salvar relatório** abre
o seletor de destino para JSON ou texto. Fechar a janela não grava nada.

### Contagens dos filtros

A linha de controles de Caches contém **Marcar Tudo**, **Desmarcar Tudo** e
**Busca**. Na linha seguinte ficam os filtros. Cada opção mostra entre parênteses
quantos alvos encontraria considerando a busca e os demais filtros ativos.
O filtro da própria opção é substituído para calcular essa possibilidade.
Exemplo: com Efeito=Recria cache e Marcação=Marcados, a opção
Condição=Disponível agora conta somente caches marcados disponíveis.

As quantidades contam os alvos individuais (linhas filhas), sem somar novamente
as linhas de agrupamento por aplicativo. Atualizam-se após busca, seleção,
alteração de filtros e análise. Opções sem resultados continuam visíveis com `(0)`.
As marcações são mantidas ao filtrar; a confirmação informa itens marcados ocultos.

> [!IMPORTANT]
> A exclusão é permanente, sem Lixeira. O aplicativo não executa limpeza ao abrir
> e não encerra processos. Modelos e mídia offline sempre começam desmarcados.

Marcação e autorização de exclusão são etapas distintas: um cache aprovado pode
ser marcado mesmo com seu aplicativo aberto. A coluna de condição mostra o
bloqueio atual, e o rodapé informa quantos itens aguardam liberação. O número PID
identifica o processo correspondente no Gerenciador de Tarefas.

Se um programa estiver aberto, feche-o normalmente antes de executar a limpeza.
Não é obrigatório repetir a análise apenas para liberar a execução: as condições
são reavaliadas ao simular ou excluir. Uma nova análise atualiza tamanhos e estado
na tela. Formatos desconhecidos, erros de validação e diretórios protegidos
continuam sem possibilidade de marcação, com o motivo no painel de detalhes.
O aplicativo reconsulta processos e condições antes de remover, inclusive após
validar os arquivos. Durante exclusões longas, reconsulta processos periodicamente.
Um cancelamento durante a limpeza termina o alvo atual antes de parar o lote.

## Decisões preservadas

| Tipo | Comportamento |
|---|---|
| Cache HTTP, compilação, gráficos, compressão | Recriado pelo aplicativo; primeiros acessos podem demorar mais |
| Modelos Whisper | Novo download necessário; uso offline ou por caminho local pode falhar até restaurá-los |
| Modelos/OCR do Chrome | Recursos podem ficar indisponíveis até novo download |
| Spotify — mídia | Pode perder downloads offline; catálogos, preferências e login são preservados; indicadores offline podem precisar ser atualizados no próprio Spotify |
| Wheels pip | Apenas arquivos com origem pública PyPI e SHA256 reconhecido; origens locais/privadas/desconhecidas são preservadas |
| Plugins Android Studio | Downloads e caches somente com IDE parada, sem `action.script` e sem overrides conhecidos |
| Gradle | Somente nomes de versão reconhecidos; preserva `modules-2`, distribuições wrapper e configurações |
| Instalador SketchUp antigo | Apenas a cópia identificada, com identidade e idade verificadas, sem referência de manutenção nem instalação/reinício pendente |
| Arquivos em Downloads | Revisão separada no botão Downloads; nunca entram na seleção de caches |
| Relatórios `.dmp` / `.mdmp` | Somente pastas de diagnóstico explicitamente listadas, com pelo menos 14 dias. Perde o histórico técnico antigo; arquivos em uso são preservados |
| SDKs, runtimes, projetos, Maven interno | Preservados; consulta de tamanho habilitada em Opções |
| NVIDIA | Não automatizado: o procedimento oficial exige intervenção no driver e reinicializações |

Não são selecionados genericamente `LocalCache`, `Temp`, `Service Worker`,
`CacheStorage`, `IndexedDB`, cookies, senhas, sessões, bancos de sincronização ou
pastas de dados completas. O emulador removido anteriormente foi uma autorização
pontual; novos dispositivos virtuais não são apagados pelo programa.

As novas opções **Pacote antigo / sobra** e **Apaga histórico técnico** são
desmarcadas por padrão e ficam em **Outros**, fora da seleção de Caches. Esses tipos
não são dados regenerados automaticamente: é preciso revisar a perda do pacote
de instalação ou do relatório antigo. O AutoCAD 2027 em Downloads fica protegido
porque vários componentes instalados o apontam como `InstallSource`. Downloads
possui revisão própria; arquivos `.log` não são apagados genericamente, pois
podem ser logs transacionais que precisam ser mantidos.

Uma pasta de aplicativo ausente no registro não é considerada sobra por si só.
Ela pode guardar preferências, licenças, projetos ou dados de uma instalação
portátil. Só são acrescentados alvos cujo descarte tenha contrato específico.

## Revisão de Downloads

Abra **Downloads** na navegação lateral.
O programa resolve a pasta Downloads da conta atual pela API do Windows,
inclusive quando redirecionada. Não usa nome de usuário fixo.

1. Escolha o prazo, de **90 dias por padrão**, e clique **Analisar diretórios**
   no topo da janela para atualizar todas as categorias.
2. A busca recursiva lista arquivos cuja **última modificação** é anterior ao
   prazo **OU** cujo tamanho é **maior que 1 GiB (1.073.741.824 bytes)**.
   Um arquivo grande recente aparece para revisão; não é excluído automaticamente.
   A data de modificação pode ter sido preservada pelo download de um arquivo
   antigo e não significa necessariamente o dia em que ele foi baixado.
3. Ajuste o prazo e analise novamente para atualizar os candidatos antigos ou
   maiores que 1 GiB. Use busca e filtros; **Detalhes** informa data, motivo da
   inclusão e impedimentos. Alterar o prazo exige nova análise.
4. Marque arquivos individualmente ou use **Marcar Tudo** após
   revisar a lista. A seleção não é lembrada entre buscas ou execuções.
5. **Revisar limpeza** apresenta confirmação da exclusão permanente,
   inclusive a quantidade de itens marcados fora do filtro. Não usa a Lixeira.

> [!CAUTION]
> Idade e tamanho não comprovam que um arquivo é lixo. Os resultados podem
> incluir fotos, documentos fiscais, projetos e programas portáteis. A função
> não classifica seu conteúdo: revise o que está marcando antes de confirmar.

São removidos somente os arquivos escolhidos; pastas ficam preservadas.
Arquivos parciais (`.crdownload`, `.part`, `.partial`, `.download`, `.tmp`),
links, pontos de junção e placeholders offline são preservados. Arquivos
inacessíveis são pulados com aviso, sem baixar conteúdo da nuvem.

Fontes e locais registrados de instalação/manutenção, incluindo MSI por usuário,
são protegidos. Uma referência interna preserva o pacote que contém `Setup.exe`;
sem esse marcador, preserva-se o diretório de primeiro nível por precaução.
Falha ao consultar registro ou processos bloqueia a exclusão. Não é possível
garantir a identificação de dependências não registradas, como programas portáteis.

Na execução, revalida pasta conhecida, caminho, tamanho, data, identidade,
critérios e processos. A remoção usa um handle exclusivo do Windows: arquivos
em uso ou alterados desde a busca são preservados. Cada operação gera um
relatório em memória, gravado somente ao clicar **Salvar relatório…** e escolher
um destino. Cancelar interrompe entre arquivos e informa
itens não processados; uma busca cancelada identifica a lista como incompleta.
O pacote AutoCAD 2024 deixou a lista de caches e passou a essa revisão separada.

## Preferências e relatórios

Acima da tabela, os filtros de **Efeito**, **Condição** e **Marcação** combinam-se
com a busca de texto. É possível, por exemplo, mostrar apenas caches que podem
ser recriados e estão disponíveis agora, ou somente modelos que exigirão download.
A condição descreve o resultado da última análise; continua sendo revalidada ao
executar a limpeza. A busca e os filtros são preservados ao trocar de categoria;
selecionar as opções **Todos** restaura a visão completa sem desmarcar.

O contador mostra quantidade e tamanho dos itens visíveis. Alterar um filtro não
desmarca os itens ocultados: o rodapé informa quantos marcados ficaram fora da
visão atual, e a confirmação avisa que eles também fazem parte do lote.
Marcar um aplicativo na tabela ou usar **Marcar Tudo** afeta somente
os itens visíveis. **Desmarcar tudo** limpa toda a seleção, inclusive os ocultos.

- **Opções**, na parte inferior da navegação lateral, permite lembrar seleções de caches e
  incluir diretórios preservados na próxima análise. Isso não autoriza execução.
- `cache-preferences.json` e `preferences.json` contêm apenas preferências e IDs de seleção de caches;
  modelos, mídia e instaladores não são lembrados como selecionados.
- Relatórios de simulação e limpeza permanecem somente em memória durante a
  sessão. Nenhum log ou pasta de logs é criado automaticamente. **Salvar
  relatório…** pede o destino e só então grava o arquivo. Fechar a aplicação
  descarta relatórios não salvos. Preferências continuam separadas dos relatórios.
- Os tamanhos são lógicos, em GiB/MiB. Podem diferir do espaço alocado ou liberado.
- Falhas de disco ou arquivos que mudem após a validação podem deixar uma limpeza
  parcial de cache. O relatório informa o alvo afetado; arquivos desconhecidos não
  são usados como alternativa para compensar espaço.

## Organização e manutenção

### Programas e sistema

Nova área de gerenciamento por instalação, com inventário de MySQL e Windows,
sugestões e ações próprias desses sistemas. [Documentação e contratos](maintenance.md).
As categorias Caches, Downloads e Outros conservam sua seleção e suas regras.

### Interface implementada

Interface aprovada e implementada como Destralha:

- Navegação lateral única para Caches, Downloads e Outros; sem abas aninhadas.
- Análise global sempre acessível no cabeçalho, com ícone e rótulo visível.
- Lista com busca, filtros com contagens combinadas e detalhes sob demanda:
  localização, consequência da exclusão e dados preservados.
- Rodapé com total marcado, volume, itens bloqueados/ocultos e revisão antes
  de executar. Seleção e limpeza continuam restritas à categoria atual.
- Progresso de análise e limpeza com etapa atual, tarefa em execução,
  cancelamento e opção de acompanhar em segundo plano. Percentuais somente
  quando mensuráveis; enumeração sem total conhecido usa estado indeterminado.
- Resultado mantido apenas em memória, com **Salvar relatório** como única
  ação que grava esse relatório em disco.

A aplicação usa o motor de limpeza real e os dados do usuário atual. O conceito
visual anterior usava dados ilustrativos; esses dados não estão incorporados à
aplicação. O projeto está em `C:\X\Destralha` e é versionado no repositório
[rodrigogml/Destralha](https://github.com/rodrigogml/Destralha). Não há serviço, agendamento ou limpeza ao abrir.

Referências visuais: [Microsoft PC Manager](https://pcmanager.microsoft.com/en-us),
[CCleaner Custom Clean](https://support.ccleaner.com/s/article/how-do-i-use-custom-clean?language=en_US)
e [CleanMyMac Smart Care](https://macpaw.com/support/cleanmymac/knowledgebase/smart-care).

Arquivos aprovados com atributo **Somente leitura** têm apenas esse atributo
retirado imediatamente antes da exclusão. O programa não toma posse nem altera
permissões (ACL). Se o arquivo pertencer a Administradores e a conta executante
não tiver escrita, será necessário abrir o programa elevado. Em falha na remoção,
tenta restaurar o atributo no mesmo arquivo, mantendo o registro da falha.

- `engine.py`: descoberta restrita, processos, condições, tamanho e exclusão.
- `catalog.json`: regras e conhecimento obtidos na análise, sem usuário fixo.
- `destralha.py`: interface principal, filtros, tarefas em threads e fila de eventos.
- `downloads.py`: análise e remoção restrita de arquivos de Downloads.
- `assets/brand/`: logo transparente, símbolo e ícone Windows.
- `tests/test_engine.py`: contratos de segurança com arquivos artificiais.
- `tests/test_destralha.py`: fluxo real da nova interface, isolamento das categorias,
  confirmação, progresso, cancelamento, simulação e exportação manual.

O catálogo tem checksum incorporado ao motor. Alterá-lo requer revisão de código
e atualização consciente desse checksum; a interface não permite adicionar
diretórios arbitrários nem desativar guardas. Padrões de descoberta são limitados
a cada nível de diretório; nunca se usa pesquisa recursiva genérica por `*cache*`.
Versões/aplicativos ainda não contemplados exigem uma nova regra revisada.

A consulta de processos usa `psutil` quando ele já estiver instalado. Caso
contrário, usa uma consulta CIM de leitura pelo PowerShell incorporado ao Windows,
sem arquivos PS1. Nenhum desses mecanismos encerra aplicativos.

Para executar os testes, em um terminal na pasta do aplicativo:

```text
python -m unittest discover -s tests -v
```

Os testes removem somente arquivos artificiais em diretórios temporários. Não
executam limpeza de AppData nem da conta real.

## Fontes dos contratos

- [Armazenamento WebView2: cache e dados persistentes](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/user-data-folder)
- [Modelos de IA do Chrome](https://developer.chrome.com/docs/ai/understand-built-in-model-management)
- [Cache pip](https://pip.pypa.io/en/stable/topics/caching/)
- [Cache npm](https://docs.npmjs.com/cli/v11/commands/npm-cache)
- [Diretórios Gradle](https://docs.gradle.org/current/userguide/directory_layout.html)
- [Cache Hugging Face](https://huggingface.co/docs/huggingface_hub/guides/manage-cache)
- [Faster Whisper](https://github.com/SYSTRAN/faster-whisper)
- [Armazenamento Spotify](https://support.spotify.com/br-pt/article/storage-information/)
- [Diretórios Android Studio](https://developer.android.com/studio/troubleshoot#directories)
- [Cache NVIDIA](https://nvidia.custhelp.com/app/answers/detail/a_id/5735/~/deleting-nvidia-shader-cache-files)
