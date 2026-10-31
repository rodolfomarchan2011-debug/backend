-- 31/10/2026 - Ofertas do site editaveis pelo painel da loja.
-- Antes: as ofertas viviam em site/js/ofertas.js (arquivo de codigo, exigia
-- programador e novo deploy). Agora ficam no banco do clube e o site le de
-- GET /api/ofertas. Idempotente: pode rodar de novo sem duplicar.

CREATE TABLE IF NOT EXISTS ofertas (
  id            VARCHAR(36)  NOT NULL PRIMARY KEY,
  produto       VARCHAR(120) NOT NULL,
  descricao     VARCHAR(400) DEFAULT NULL,
  categoria     VARCHAR(60)  DEFAULT NULL,
  preco_de      DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  preco_por     DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  unidade       VARCHAR(12)  NOT NULL DEFAULT 'kg',
  desconto      INT          DEFAULT NULL,
  imagem_url    VARCHAR(255) DEFAULT NULL,
  imagem_mime   VARCHAR(60)  DEFAULT NULL,
  imagem_bytes  MEDIUMBLOB   DEFAULT NULL,
  destaque      TINYINT(1)   NOT NULL DEFAULT 0,
  ativo         TINYINT(1)   NOT NULL DEFAULT 1,
  ordem         INT          NOT NULL DEFAULT 0,
  criado_em     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  atualizado_em DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_ofertas_listagem (ativo, ordem)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Semente: as 4 ofertas que ja estavam publicadas em js/ofertas.js,
-- para o site continuar identico no primeiro deploy.
INSERT IGNORE INTO ofertas
  (id, produto, descricao, categoria, preco_de, preco_por, unidade, desconto, imagem_url, destaque, ativo, ordem)
VALUES
  ('picanha', 'Picanha bovina',
   'Peça inteira, macia e com capa de gordura no ponto certo para o churrasco.',
   'Carnes nobres', 89.90, 71.90, 'kg', 20, 'assets/img/produtos/picanha.svg', 1, 1, 1),
  ('costela', 'Costela bovina',
   'Costela para assar lentamente, suculenta e cheia de sabor.',
   'Churrasco', 39.90, 32.90, 'kg', 18, 'assets/img/produtos/costela.svg', 1, 1, 2),
  ('frango', 'Frango inteiro',
   'Frango resfriado, ideal para o almoço de domingo em família.',
   'Aves', 19.90, 16.90, 'kg', 15, 'assets/img/produtos/frango.svg', 1, 1, 3),
  ('linguica', 'Linguiça artesanal',
   'Receita da casa, temperada na medida para a grelha.',
   'Embutidos', 34.90, 29.90, 'kg', 14, 'assets/img/produtos/linguica.svg', 0, 1, 4);
