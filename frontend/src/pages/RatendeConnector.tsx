import { Card, PageHeader } from '../components/ui'

// produto-15 -- página dedicada à extensão (pedido do dono 26/08/2026:
// antes era só um card no Início, agora é rota própria no menu com tudo
// sobre a extensão: instaladores por SO, alternativa dev-mode, o que a
// extensão faz, permissões, e o aviso "gerenciado pela organização"
// (pergunta real do dono sobre por que aparece e o que muda ao
// desinstalar). Instaladores per-user (HKCU/plist por usuário/policy
// Linux) -- sem admin/sudo, só vale pro usuário que instalou.
const BUCKET = 'https://storage.googleapis.com/rangel-tech-ratende-connector'

function DownloadCard({
  titulo,
  descricao,
  href,
  destaque = false,
}: {
  titulo: string
  descricao: string
  href: string
  destaque?: boolean
}) {
  return (
    <a
      href={href}
      className={`block rounded-[20px] border px-5 py-4 shadow-[0_12px_40px_-32px_rgba(15,23,42,0.4)] transition hover:border-[var(--brand)] ${
        destaque ? 'border-[var(--brand)] bg-[var(--brand-soft)]' : 'border-[var(--border)] bg-[var(--surface-solid)]'
      }`}
    >
      <p className="text-sm font-semibold text-[var(--text)]">{titulo}</p>
      <p className="mt-1 text-sm leading-6 text-[var(--text-muted)]">{descricao}</p>
    </a>
  )
}

export default function RatendeConnector() {
  return (
    <div className="space-y-8">
      <PageHeader
        title="RAtende Connector"
        description="Extensão de navegador para conectar contas Instagram, Facebook e TikTok direto do Chrome/Edge, sem depender do navegador remoto. Ainda em fase experimental."
      />

      <Card title="Windows">
        <div className="grid gap-3 sm:grid-cols-2">
          <DownloadCard
            destaque
            titulo="Instalador (.msi)"
            descricao="Formato corporativo, msiexec cuida de tudo. Sem UAC — instala só pro seu usuário."
            href={`${BUCKET}/RAtende-Connector-Instalador.msi`}
          />
          <DownloadCard
            titulo="Instalador (.exe)"
            descricao="Wizard NSIS, alternativa ao MSI. Também sem UAC."
            href={`${BUCKET}/RAtende-Connector-Instalador.exe`}
          />
        </div>
      </Card>

      <Card title="macOS">
        <div className="grid gap-3 sm:grid-cols-2">
          <DownloadCard
            titulo="Instalar (.sh)"
            descricao="Sem sudo — escreve a política no seu usuário via defaults write. Rode no Terminal: sh instalar-ratende-connector-mac.sh"
            href={`${BUCKET}/instalar-ratende-connector-mac.sh`}
          />
          <DownloadCard
            titulo="Desinstalar (.sh)"
            descricao="Remove a política e a extensão some sozinha do Chrome."
            href={`${BUCKET}/desinstalar-ratende-connector-mac.sh`}
          />
        </div>
      </Card>

      <Card title="Linux">
        <div className="grid gap-3 sm:grid-cols-2">
          <DownloadCard
            titulo="Pacote (.deb)"
            descricao="Debian, Ubuntu, Mint, Pop!_OS e derivados. dpkg -i ou clique duplo no gerenciador de pacotes."
            href={`${BUCKET}/ratende-connector-installer.deb`}
          />
          <DownloadCard
            titulo="Instalador universal (.sh)"
            descricao="Qualquer distro (Fedora, Arch, openSUSE...). Mesma política, sem empacotamento específico."
            href={`${BUCKET}/instalar-ratende-connector.sh`}
          />
        </div>
      </Card>

      <Card title="Carregar manualmente (modo desenvolvedor)">
        <div className="space-y-3">
          <p className="text-sm leading-6 text-[var(--text-muted)]">
            Não quer rodar um instalador? Baixe o pacote descompactado e carregue direto no navegador —
            fica visível como extensão "sem verificação", sem política nenhuma.
          </p>
          <DownloadCard
            titulo="Pacote descompactado (.zip)"
            descricao="Extraia numa pasta, abra chrome://extensions, ative o Modo desenvolvedor e clique em Carregar sem compactação."
            href={`${BUCKET}/ratende-connector.zip`}
          />
        </div>
      </Card>

      <Card title="Como funciona">
        <div className="space-y-4 text-sm leading-6 text-[var(--text-muted)]">
          <p>
            Todo instalador faz a mesma coisa: escreve uma política do navegador (
            <code className="rounded bg-[var(--surface-soft)] px-1.5 py-0.5">ExtensionInstallForcelist</code>) que
            aponta pra um manifest de atualização hospedado por nós. O Chrome/Edge lê essa política sozinho, baixa a
            extensão e mantém ela atualizada — sem "Modo desenvolvedor" nem Chrome Web Store.
          </p>
          <div className="rounded-[16px] border border-[var(--border)] bg-[var(--surface-soft)] p-4">
            <p className="font-semibold text-[var(--text)]">Por que aparece "Gerenciado pela sua organização"?</p>
            <p className="mt-2">
              Qualquer política de navegador (mesmo essa única) ativa esse aviso no menu e em{' '}
              <code className="rounded bg-[var(--surface-elevated)] px-1.5 py-0.5">chrome://management</code>. Não
              tem como instalar por esse mecanismo sem o aviso aparecer — é assim que o Chrome sinaliza qualquer
              navegador com política aplicada, mesmo local. Se o computador já for gerenciado de verdade pela
              empresa (Google Workspace, GPO), a política deles tem prioridade e pode ignorar a nossa.
            </p>
          </div>
          <div className="rounded-[16px] border border-[var(--border)] bg-[var(--surface-soft)] p-4">
            <p className="font-semibold text-[var(--text)]">Desinstalar remove a extensão também</p>
            <p className="mt-2">
              A extensão fica amarrada à política que instalou ela — não dá pra remover manualmente enquanto a
              política existir. Rodar o desinstalador apaga a política e o navegador desinstala a extensão sozinho
              no próximo início, sem deixar rastro.
            </p>
          </div>
          <div className="rounded-[16px] border border-[var(--border)] bg-[var(--surface-soft)] p-4">
            <p className="font-semibold text-[var(--text)]">Permissões pedidas</p>
            <p className="mt-2">
              Acesso a cookies e abas apenas nos domínios instagram.com, facebook.com e tiktok.com (pra detectar a
              sessão depois de um login manual do usuário) e ao domínio do RAgentes (pra enviar a sessão pro seu
              tenant). Nenhum outro site é acessado.
            </p>
          </div>
          <p className="text-xs text-[var(--text-faint)]">
            Ainda sem certificado de assinatura de código: o instalador do Windows pode disparar um aviso do
            Defender/SmartScreen na primeira execução.
          </p>
        </div>
      </Card>
    </div>
  )
}
