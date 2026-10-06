# Color Retouch 1.1.0

Retoca los colores de una pieza en Blender **sin salir del modo Objeto**.
Cada color es un material de la pieza; pintar cambia el material de las caras.

## Dónde
Vista 3D → tecla **N** → pestaña **Color Retouch**.

## Uso
1. Selecciona la pieza.
2. **Colores de la pieza**: lista de colores (1, 2, 3…). El marcado es con el que pintas.
   - Cambia un color tocando su muestra.
   - **Color nuevo** → elige color y nombre → **Añadir color**.
3. **Herramienta**: Pincel, Cubo o Cuentagotas → **Retocar**.
4. En la vista:
   - Clic izquierdo: pinta (pincel, arrastrando) · rellena la zona (cubo) · coge el color (cuentagotas).
   - Botón central: girar la vista · rueda: zoom.
   - `B` pincel · `F` cubo · `I` cuentagotas · `1`–`9` cambia de color.
   - `[` `]` o Shift + rueda: tamaño del pincel.
   - Ctrl+Z: deshace la última pincelada.
   - Esc, Intro o clic derecho: terminar.
5. **Guardar**.

**Atravesar** (activado): el pincel pinta también la parte de atrás de tubos y piezas finas.

## Pintar con precisión (hasta una arista o una línea)
- **Parar en aristas** (pincel, tecla `E`): el pincel no pasa de un borde doblado
  (esquina, canto de un panel, unión de un tubo). Pinta hasta el borde sin salirse.
- **Cubo → Rellenar hasta: Arista**: un clic pinta todo el panel hasta sus bordes,
  aunque ahora tenga manchas de varios colores.
- **Ángulo de arista**: cuánto se tiene que doblar la superficie para contar como borde.
  Bajo (10–20°) = para en curvas suaves · alto (45–60°) = solo en esquinas marcadas.
- **Shift + clic** (pincel): línea recta desde el último punto pintado. Para seguir un canto:
  pincel pequeño, clic en un extremo y Shift + clic en el otro.
- Pincel pequeño (`[`) y acercar con la rueda para los detalles finos.

## Reglas
- El original no se toca: la primera vez se crea `<pieza>_color` y el original se oculta.
  Si la pieza ya es una copia de trabajo de otro complemento del taller, se pinta directamente.
- La pieza no puede tener modificadores activos.
