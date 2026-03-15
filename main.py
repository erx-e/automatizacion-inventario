#!/usr/bin/env python3
# main.py — Punto de entrada para probar el motor localmente
import sys
import json
from config import validar_configuracion
from motor import (
    ejecutar_cierre, solo_parsear_ticket, solo_consumo_teorico,
    preparar_cierre, confirmar_cierre, corregir_inventario_por_insumos,
    preparar_correccion, confirmar_correccion,
    preparar_inventario_registros, confirmar_inventario_registros,
    preparar_solo_ventas, confirmar_solo_ventas,
)


def _leer_rollitos_override(argv: list[str]) -> dict | None:
    tiene_pollo = "--rollitos-pollo" in argv
    tiene_queso = "--rollitos-queso" in argv
    if not tiene_pollo and not tiene_queso:
        return None

    override = {"pollo": 0, "queso": 0}
    if tiene_pollo:
        idx = argv.index("--rollitos-pollo")
        if idx + 1 >= len(argv):
            raise ValueError("Falta valor para --rollitos-pollo")
        override["pollo"] = int(argv[idx + 1])

    if tiene_queso:
        idx = argv.index("--rollitos-queso")
        if idx + 1 >= len(argv):
            raise ValueError("Falta valor para --rollitos-queso")
        override["queso"] = int(argv[idx + 1])

    return override


def _leer_insumos(argv: list[str], flag: str) -> list[str] | None:
    if flag not in argv:
        return None

    idx = argv.index(flag)
    if idx + 1 >= len(argv):
        raise ValueError(f"Falta valor para {flag}")

    insumos = [item.strip() for item in argv[idx + 1].split(",") if item.strip()]
    if not insumos:
        raise ValueError(f"Debes indicar al menos un insumo en {flag}")
    return insumos


def _leer_fecha(argv: list[str]) -> str | None:
    if "--fecha" not in argv:
        return None
    idx = argv.index("--fecha")
    if idx + 1 < len(argv):
        return argv[idx + 1]
    return None


def main():
    if len(sys.argv) < 2:
        print("Uso:")
        print("  python3 main.py <imagen>                     → Cierre completo (sin confirmación)")
        print("  python3 main.py <imagen> --solo-leer          → Solo parsear el ticket")
        print("  python3 main.py <imagen> --consumo            → Solo consumo teórico")
        print("  python3 main.py <imagen> --preparar           → Preparar cierre (pide confirmación)")
        print("  python3 main.py <imagen> --solo-ventas        → Solo cargar ventas a entrada existente")
        print("  python3 main.py --solo-registros              → Inventario solo desde registros (sin foto)")
        print("  python3 main.py <imagen> --fecha 2026-03-11   → Con fecha específica")
        sys.exit(1)

    try:
        validar_configuracion()
    except EnvironmentError as e:
        print(f"❌ {str(e)}")
        sys.exit(1)

    # --solo-registros no requiere imagen
    if "--solo-registros" in sys.argv:
        fecha = _leer_fecha(sys.argv)
        confirmar = "--confirmar" in sys.argv
        prep = preparar_inventario_registros(fecha=fecha)
        print(prep["resumen"])
        if not prep["ok"]:
            return
        if confirmar:
            print("\n" + confirmar_inventario_registros(prep))
        elif sys.stdin.isatty():
            respuesta = input("\n> ").strip().lower()
            if respuesta in ("si", "dale", "ok"):
                print("\n" + confirmar_inventario_registros(prep))
            else:
                print("❌ Cancelado.")
        return

    if len(sys.argv) < 2 or sys.argv[1].startswith("--"):
        print("❌ Falta la ruta de la imagen del ticket.")
        sys.exit(1)

    image_path = sys.argv[1]
    fecha = _leer_fecha(sys.argv)
    usar_registros_rollitos = "--usar-registros-rollitos" in sys.argv
    try:
        rollitos_override = _leer_rollitos_override(sys.argv)
        insumos_correccion = _leer_insumos(sys.argv, "--corregir-insumos")
        insumos_preparar_correccion = _leer_insumos(sys.argv, "--preparar-correccion")
    except ValueError as e:
        print(f"❌ {str(e)}")
        sys.exit(1)

    if "--solo-leer" in sys.argv:
        print(solo_parsear_ticket(image_path=image_path))
        return

    if "--consumo" in sys.argv:
        print(solo_consumo_teorico(
            image_path=image_path,
            fecha=fecha,
            rollitos_override=rollitos_override,
            usar_registros_rollitos=usar_registros_rollitos,
        ))
        return

    if "--solo-ventas" in sys.argv:
        prep = preparar_solo_ventas(
            image_path=image_path,
            fecha=fecha,
            rollitos_override=rollitos_override,
        )
        print(prep["resumen"])
        if not prep["ok"]:
            return
        confirmar = "--confirmar" in sys.argv
        if confirmar:
            print("\n" + confirmar_solo_ventas(prep))
        elif sys.stdin.isatty():
            respuesta = input("\n> ").strip().lower()
            if respuesta in ("si", "dale", "ok"):
                print("\n" + confirmar_solo_ventas(prep))
            else:
                print("❌ Cancelado.")
        return

    if "--preparar" in sys.argv:
        prep = preparar_cierre(
            image_path=image_path,
            fecha=fecha,
            rollitos_override=rollitos_override,
        )
        print(prep["resumen"])

        if not prep["ok"]:
            return

        # Solo pedir confirmación interactiva si hay terminal
        if not sys.stdin.isatty():
            return

        respuesta = input("\n> ").strip().lower()

        if respuesta == "si":
            print("\n" + confirmar_cierre(
                prep,
                image_path=image_path,
                rollitos_override=rollitos_override,
            ))
        elif respuesta.startswith("fecha "):
            nueva_fecha = respuesta.replace("fecha ", "").strip()
            print(f"\n📅 Cambiando fecha a {nueva_fecha}...")
            print("\n" + confirmar_cierre(
                prep,
                fecha_override=nueva_fecha,
                image_path=image_path,
                rollitos_override=rollitos_override,
            ))
        else:
            print("❌ Cierre cancelado.")
        return

    if "--preparar-correccion" in sys.argv:
        prep = preparar_correccion(
            image_path=image_path,
            fecha=fecha,
            insumos=insumos_preparar_correccion,
            rollitos_override=rollitos_override,
        )
        print(prep["resumen"])
        if not prep["ok"]:
            return

        if not sys.stdin.isatty():
            return

        respuesta = input("\n> ").strip().lower()
        if respuesta == "si":
            print("\n" + confirmar_correccion(prep))
        else:
            print("❌ Corrección cancelada.")
        return

    if "--corregir-insumos" in sys.argv:
        print(corregir_inventario_por_insumos(
            image_path=image_path,
            fecha=fecha,
            insumos=insumos_correccion,
            rollitos_override=rollitos_override,
        ))
        return

    # Cierre directo sin confirmación (legacy)
    print(ejecutar_cierre(
        image_path=image_path,
        fecha=fecha,
        rollitos_override=rollitos_override,
    ))


if __name__ == "__main__":
    main()
