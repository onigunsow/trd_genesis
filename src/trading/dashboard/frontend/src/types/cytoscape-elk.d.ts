// cytoscape-elk 는 타입 선언을 배포하지 않는다. cytoscape.Ext(cytoscape.use 인자) 형태로
// export default 하는 최소 shim만 선언한다.
declare module 'cytoscape-elk' {
  import type cytoscape from 'cytoscape'
  const ext: cytoscape.Ext
  export default ext
}
