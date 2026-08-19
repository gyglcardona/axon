"""
Pruebas de src/orquestador.py::registrar_empresa_nueva -- el autorregistro
público de una empresa nueva (sin sesión, sin que nadie la invite primero).
Es el único endpoint público que crea datos persistentes, así que estas
pruebas se enfocan en los ángulos de seguridad: el rol nunca escala, un NIT
no sirve para escapar del directorio de configuración (path traversal), no
se puede duplicar NIT/correo, y hay un freno contra abuso por correo.
Nunca toca red real -- correo.enviar_correo se reemplaza por un grabador.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import auth  # noqa: E402
import auth_store  # noqa: E402
import orquestador  # noqa: E402
import state_store  # noqa: E402


@pytest.fixture
def entorno_registro(tmp_path, monkeypatch):
    registro = tmp_path / "registro.json"
    registro.write_text(json.dumps({"empresas": []}), encoding="utf-8")
    monkeypatch.setattr(orquestador, "REGISTRO", registro)
    monkeypatch.setattr(orquestador, "CONFIG_EMPRESAS_DIR", tmp_path / "config" / "empresas")
    monkeypatch.setattr(orquestador, "BASE_DATOS_EMPRESAS", tmp_path / "data" / "empresas")

    original_conectar_empresa = state_store.conectar
    monkeypatch.setattr(
        state_store, "conectar",
        lambda nit, base_dir=None: original_conectar_empresa(nit, base_dir=tmp_path / "data" / "empresas"),
    )

    original_conectar_sistema = auth_store.conectar
    monkeypatch.setattr(orquestador.auth_store, "conectar", lambda base_dir=None: original_conectar_sistema(base_dir=tmp_path))

    enviados = []
    monkeypatch.setattr(auth.correo, "enviar_correo", lambda destinatario, asunto, cuerpo: enviados.append(
        {"destinatario": destinatario, "asunto": asunto, "cuerpo": cuerpo},
    ))
    return {"tmp_path": tmp_path, "registro": registro, "enviados": enviados}


def _leer_registro(registro_path):
    with open(registro_path, encoding="utf-8") as f:
        return json.load(f)


def test_registro_exitoso_crea_empresa_usuario_y_envia_correo(entorno_registro):
    resultado = orquestador.registrar_empresa_nueva("900.123.456-7", "Panadería La Espiga S.A.S.", "Dueno@Empresa.com")

    assert resultado["registrado"] is True
    assert resultado["nit"] == "9001234567"  # se despoja de puntos/DV -- solo dígitos
    assert resultado["slug"] == "panaderia-la-espiga-s-a-s"

    datos = _leer_registro(entorno_registro["registro"])
    assert len(datos["empresas"]) == 1
    assert datos["empresas"][0]["nit"] == "9001234567"

    assert len(entorno_registro["enviados"]) == 1
    assert entorno_registro["enviados"][0]["destinatario"] == "dueno@empresa.com"

    conn = auth_store.conectar(base_dir=entorno_registro["tmp_path"])
    usuario = auth_store.obtener_usuario_por_email(conn, "dueno@empresa.com")
    try:
        assert usuario["rol"] == "empresa"
        assert usuario["puede_crear_usuarios"] is False
        assert usuario["password_hash"] is None  # no puede iniciar sesión hasta confirmar el correo
        assert auth_store.listar_nits_de_usuario(conn, usuario["id"]) == ["9001234567"]
    finally:
        conn.close()


def test_registro_materializa_base_de_datos_aislada_de_la_empresa(entorno_registro):
    orquestador.registrar_empresa_nueva("900123456", "Empresa Nueva", "a@b.com")
    assert (entorno_registro["tmp_path"] / "data" / "empresas" / "900123456.db").is_file()


def test_registro_crea_md_de_referencia(entorno_registro):
    orquestador.registrar_empresa_nueva("900123456", "Empresa Nueva", "a@b.com")
    md = entorno_registro["tmp_path"] / "config" / "empresas" / "900123456.md"
    assert md.is_file()
    assert "Empresa Nueva" in md.read_text(encoding="utf-8")


def test_registro_nunca_crea_config_json_de_credenciales(entorno_registro):
    """config/empresas/<nit>.json (credenciales Siigo, políticas) se sigue
    creando perezosamente desde 'Configuración' -- nunca en el registro."""
    orquestador.registrar_empresa_nueva("900123456", "Empresa Nueva", "a@b.com")
    assert not (entorno_registro["tmp_path"] / "config" / "empresas" / "900123456.json").exists()


def test_registro_rol_siempre_es_empresa_sin_importar_que_intente_mandar_el_cliente(entorno_registro):
    """`registrar_empresa_nueva` ni siquiera acepta un parámetro de rol --
    esta prueba documenta esa garantía estructural (no hay forma de que un
    request público cree un superusuario o contador)."""
    import inspect
    firma = inspect.signature(orquestador.registrar_empresa_nueva)
    assert "rol" not in firma.parameters
    assert "puede_crear_usuarios" not in firma.parameters

    orquestador.registrar_empresa_nueva("900123456", "Empresa Nueva", "a@b.com")
    conn = auth_store.conectar(base_dir=entorno_registro["tmp_path"])
    try:
        usuario = auth_store.obtener_usuario_por_email(conn, "a@b.com")
        assert usuario["rol"] == "empresa"
    finally:
        conn.close()


def test_registro_rechaza_nit_duplicado(entorno_registro):
    orquestador.registrar_empresa_nueva("900123456", "Primera Empresa", "primero@empresa.com")
    with pytest.raises(ValueError, match="Ya existe una empresa registrada"):
        orquestador.registrar_empresa_nueva("900.123.456", "Otra Empresa Con Mismo NIT", "segundo@empresa.com")

    datos = _leer_registro(entorno_registro["registro"])
    assert len(datos["empresas"]) == 1  # el segundo intento no dejó rastro


def test_registro_rechaza_correo_duplicado(entorno_registro):
    orquestador.registrar_empresa_nueva("900111111", "Empresa Uno", "misma@empresa.com")
    with pytest.raises(ValueError, match="correo"):
        orquestador.registrar_empresa_nueva("900222222", "Empresa Dos", "misma@empresa.com")

    datos = _leer_registro(entorno_registro["registro"])
    assert len(datos["empresas"]) == 1  # el segundo NIT nunca se agregó al registro


@pytest.mark.parametrize("nit_malicioso", [
    "../../etc/passwd",
    "..\\..\\config\\sistema",
    "abc",
    "",
])
def test_registro_rechaza_nit_sin_digitos_suficientes(entorno_registro, nit_malicioso):
    """Estos valores no dejan ni 5 dígitos al despojarlos de todo lo que no
    sea número -- se rechazan directamente en vez de intentar usarlos como
    nombre de archivo."""
    with pytest.raises(ValueError, match="NIT"):
        orquestador.registrar_empresa_nueva(nit_malicioso, "Empresa Cualquiera", "a@b.com")
    # la validación falla antes de tocar disco -- ni el directorio se crea
    assert not (entorno_registro["tmp_path"] / "config" / "empresas").exists()


def test_registro_despoja_caracteres_no_numericos_del_nit_en_vez_de_usarlos_crudos(entorno_registro):
    """Un NIT con basura alrededor (separadores, intentos de inyección) se
    sanea a solo-dígitos antes de convertirse en nombre de archivo -- nunca
    se usa el valor crudo del formulario en una ruta."""
    resultado = orquestador.registrar_empresa_nueva("900123456; rm -rf", "Empresa Cualquiera", "a@b.com")
    assert resultado["nit"] == "900123456"
    assert (entorno_registro["tmp_path"] / "config" / "empresas" / "900123456.md").is_file()


def test_registro_rechaza_email_con_formato_invalido(entorno_registro):
    with pytest.raises(ValueError, match="correo"):
        orquestador.registrar_empresa_nueva("900123456", "Empresa Nueva", "no-es-un-correo")


def test_registro_rechaza_razon_social_vacia(entorno_registro):
    with pytest.raises(ValueError, match="razón social"):
        orquestador.registrar_empresa_nueva("900123456", "   ", "a@b.com")


def test_registro_limite_de_intentos_por_correo(entorno_registro):
    """Máximo 3 intentos por correo por hora -- evita bombardear un correo
    ajeno de invitaciones repitiendo el registro con NITs distintos."""
    # el intento 1 tiene éxito y ya deja el correo "ocupado" en usuarios
    orquestador.registrar_empresa_nueva("900000001", "Empresa Uno", "spam@objetivo.com")
    for n in range(2, orquestador.LIMITE_INTENTOS_REGISTRO_EMPRESA + 1):
        with pytest.raises(ValueError, match="correo"):
            orquestador.registrar_empresa_nueva(f"90000000{n}", f"Empresa {n}", "spam@objetivo.com")

    # el intento número 4 debe rechazarse por el límite, no llegar a crear nada
    with pytest.raises(ValueError, match="Demasiados intentos"):
        orquestador.registrar_empresa_nueva("900000099", "Empresa Extra", "spam@objetivo.com")

    datos = _leer_registro(entorno_registro["registro"])
    assert len(datos["empresas"]) == 1  # solo el primer registro exitoso


def test_registro_genera_slugs_unicos_cuando_el_nombre_se_repite(entorno_registro):
    orquestador.registrar_empresa_nueva("900000001", "Distribuidora Central", "uno@empresa.com")
    resultado2 = orquestador.registrar_empresa_nueva("900000002", "Distribuidora Central", "dos@empresa.com")
    assert resultado2["slug"] == "distribuidora-central-2"


# --- orquestador.crear_empresa_administrada ---

def _crear_actor(entorno, email, rol, puede_crear_usuarios=False):
    conn = auth_store.conectar(base_dir=entorno["tmp_path"])
    try:
        usuario_id = auth_store.crear_usuario(conn, email, rol, puede_crear_usuarios)
        return auth_store.obtener_usuario_por_id(conn, usuario_id)
    finally:
        conn.close()


def test_crear_empresa_administrada_sin_permiso_da_error(entorno_registro):
    auxiliar = _crear_actor(entorno_registro, "aux@firma.com", "contador", puede_crear_usuarios=False)
    with pytest.raises(auth.AuthError, match="permiso"):
        orquestador.crear_empresa_administrada("900123456", "Empresa Nueva", "cliente@empresa.com", auxiliar)
    assert _leer_registro(entorno_registro["registro"])["empresas"] == []


def test_crear_empresa_administrada_superusuario_no_queda_autoasociado(entorno_registro):
    """El superusuario ya ve todas las empresas por diseño -- no necesita
    (ni debe acumular) una fila de asociación explícita por cada una."""
    super_usuario = _crear_actor(entorno_registro, "admin@axon.com", "superusuario")

    resultado = orquestador.crear_empresa_administrada(
        "900123456", "Empresa Nueva S.A.S.", "cliente@empresa.com", super_usuario,
    )

    assert resultado == {"creado": True, "slug": "empresa-nueva-s-a-s", "nit": "900123456"}
    conn = auth_store.conectar(base_dir=entorno_registro["tmp_path"])
    try:
        assert auth_store.listar_nits_de_usuario(conn, super_usuario["id"]) == []
        dueno = auth_store.obtener_usuario_por_email(conn, "cliente@empresa.com")
        assert dueno["rol"] == "empresa"
        assert dueno["password_hash"] is None
        assert dueno["creado_por_usuario_id"] == super_usuario["id"]
        assert auth_store.listar_nits_de_usuario(conn, dueno["id"]) == ["900123456"]
    finally:
        conn.close()


def test_crear_empresa_administrada_contador_queda_autoasociado(entorno_registro):
    """Pedido explícito del usuario: si un contador crea la empresa, debe
    quedarle asignada a él también -- para que otros contadores sin
    asociación explícita no la vean."""
    contador = _crear_actor(entorno_registro, "contador@firma.com", "contador", puede_crear_usuarios=True)

    orquestador.crear_empresa_administrada("900123456", "Empresa Nueva", "cliente@empresa.com", contador)

    conn = auth_store.conectar(base_dir=entorno_registro["tmp_path"])
    try:
        assert auth_store.listar_nits_de_usuario(conn, contador["id"]) == ["900123456"]
        dueno = auth_store.obtener_usuario_por_email(conn, "cliente@empresa.com")
        assert dueno["creado_por_usuario_id"] == contador["id"]
    finally:
        conn.close()


def test_crear_empresa_administrada_otro_contador_no_ve_la_empresa_ajena(entorno_registro):
    contador_a = _crear_actor(entorno_registro, "a@firma.com", "contador", puede_crear_usuarios=True)
    contador_b = _crear_actor(entorno_registro, "b@firma.com", "contador", puede_crear_usuarios=True)

    orquestador.crear_empresa_administrada("900123456", "Empresa de A", "cliente@empresa.com", contador_a)

    conn = auth_store.conectar(base_dir=entorno_registro["tmp_path"])
    try:
        assert auth_store.listar_nits_de_usuario(conn, contador_b["id"]) == []
    finally:
        conn.close()


def test_crear_empresa_administrada_envia_invitacion_al_correo_del_cliente_no_al_del_creador(entorno_registro):
    contador = _crear_actor(entorno_registro, "contador@firma.com", "contador", puede_crear_usuarios=True)

    orquestador.crear_empresa_administrada("900123456", "Empresa Nueva", "cliente@empresa.com", contador)

    assert len(entorno_registro["enviados"]) == 1
    assert entorno_registro["enviados"][0]["destinatario"] == "cliente@empresa.com"


def test_crear_empresa_administrada_sin_limite_de_intentos_por_correo(entorno_registro):
    """A diferencia de `registrar_empresa_nueva`, no hay freno anti-abuso --
    quien llama ya pasó por login y por el chequeo de permiso, no es un
    formulario público anónimo."""
    contador = _crear_actor(entorno_registro, "contador@firma.com", "contador", puede_crear_usuarios=True)
    for n in range(1, orquestador.LIMITE_INTENTOS_REGISTRO_EMPRESA + 3):
        orquestador.crear_empresa_administrada(
            f"90000{n:04d}", f"Empresa {n}", f"cliente{n}@empresa.com", contador,
        )
    datos = _leer_registro(entorno_registro["registro"])
    assert len(datos["empresas"]) == orquestador.LIMITE_INTENTOS_REGISTRO_EMPRESA + 2


def test_crear_empresa_administrada_rechaza_nit_duplicado(entorno_registro):
    contador = _crear_actor(entorno_registro, "contador@firma.com", "contador", puede_crear_usuarios=True)
    orquestador.crear_empresa_administrada("900123456", "Primera", "uno@empresa.com", contador)
    with pytest.raises(ValueError, match="Ya existe una empresa registrada"):
        orquestador.crear_empresa_administrada("900123456", "Segunda", "dos@empresa.com", contador)


# --- orquestador.eliminar_empresa ---

def test_eliminar_empresa_borra_registro_config_md_y_base_de_datos(entorno_registro):
    resultado = orquestador.registrar_empresa_nueva("900123456", "Empresa A Borrar", "dueno@empresa.com")
    slug = resultado["slug"]
    tmp_path = entorno_registro["tmp_path"]
    md = tmp_path / "config" / "empresas" / "900123456.md"
    db = tmp_path / "data" / "empresas" / "900123456.db"
    assert md.is_file() and db.is_file()

    salida = orquestador.eliminar_empresa(slug)

    assert salida == {"eliminada": True, "nit": "900123456", "slug": slug}
    assert not md.exists()
    assert not db.exists()
    assert _leer_registro(entorno_registro["registro"])["empresas"] == []
    with pytest.raises(orquestador.EmpresaNoEncontrada):
        orquestador.resolver_empresa(slug)


def test_eliminar_empresa_quita_el_acceso_de_los_usuarios_que_la_veian(entorno_registro):
    resultado = orquestador.registrar_empresa_nueva("900123456", "Empresa A Borrar", "dueno@empresa.com")

    orquestador.eliminar_empresa(resultado["slug"])

    conn = auth_store.conectar(base_dir=entorno_registro["tmp_path"])
    try:
        usuario = auth_store.obtener_usuario_por_email(conn, "dueno@empresa.com")
        assert auth_store.listar_nits_de_usuario(conn, usuario["id"]) == []
    finally:
        conn.close()


def test_eliminar_empresa_no_toca_los_zip_originales_de_entrada_dian(entorno_registro, monkeypatch):
    """data/entrada-dian/<slug>/ son los ZIP que entregó la empresa -- no se
    borran aunque se elimine la empresa del registro (valor de auditoría
    propio, ver docs/06-multiempresa-saas/aislamiento-datos.md)."""
    tmp_path = entorno_registro["tmp_path"]
    monkeypatch.setattr(orquestador, "ENTRADA_DIAN", tmp_path / "data" / "entrada-dian")
    resultado = orquestador.registrar_empresa_nueva("900123456", "Empresa A Borrar", "dueno@empresa.com")
    carpeta_zip = tmp_path / "data" / "entrada-dian" / resultado["slug"] / "2026" / "01"
    carpeta_zip.mkdir(parents=True)
    (carpeta_zip / "factura.zip").write_bytes(b"contenido")

    orquestador.eliminar_empresa(resultado["slug"])

    assert (carpeta_zip / "factura.zip").is_file()


def test_eliminar_empresa_inexistente_da_error_claro(entorno_registro):
    with pytest.raises(orquestador.EmpresaNoEncontrada):
        orquestador.eliminar_empresa("no-existe")
