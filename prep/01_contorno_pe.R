# ==============================================================================
# Gera data/geo/contorno_pe.geojson: o contorno do estado (municipios
# dissolvidos, codarea = "UF26"). painel/dados.py junta essa feicao ao fim da
# malha BASE_MUN e o mapa desenha so a borda dela, por cima, para o recorte
# nao "sumir" no fundo branco quando os municipios estao na cor mais clara.
#
# Fernando de Noronha fica de fora: no mapa ele e um encarte deslocado
# (ENCARTE_NORONHA em painel/dados.py), entao o contorno real dele cairia no
# mar, longe do desenho.
#
# Em R porque o ambiente Python do painel nao tem shapely/geopandas, e isto
# so roda quando a malha mudar (o arquivo vai para o git):
#   Rscript prep/01_contorno_pe.R
# ==============================================================================

sf::sf_use_s2(FALSE)

mun <- sf::st_read(file.path("data", "geo", "MUN_26.geojson"), quiet = TRUE)
mun <- mun[mun$codarea != "2605459", ]

pe <- sf::st_union(sf::st_make_valid(mun))
pe <- sf::st_simplify(pe, dTolerance = 0.0005)

saida <- file.path("data", "geo", "contorno_pe.geojson")
unlink(saida)
sf::st_write(sf::st_sf(codarea = "UF26", geometry = pe), saida, driver = "GeoJSON",
             layer_options = "COORDINATE_PRECISION=5", quiet = TRUE)
message(sprintf("%s: %d pontos.", saida, nrow(sf::st_coordinates(pe))))
