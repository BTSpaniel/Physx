// SPDX-License-Identifier: MIT
/** Independent linear-field fixture crossing a negative sparse-block boundary. */
export async function verifyVelocityGather(host, { boundaries = false } = {}) {
    const { device } = host, savedOutput = host.output, savedBlocks = host.stats.activeBlocks;
    const table = new Uint32Array(138), level = new Uint32Array(32);
    level.set([1, 1, 1, 8, 1, 1, 1, 2, 3, 3, 3, 64, 2, 4, 2, 128]);
    level[18] = 136;
    table.set([1, 2], 0); table.set([0, 1], 6);
    table.set([0xffffffff, 0, 0, 0, 0, 0, 0, 0], 128);
    table.set([0x80000000, 0x80000002], 136);
    const buffer = device.createBuffer({ size: table.byteLength, usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST });
    const texture = device.createTexture({ size: [8, 4, 4], dimension: '3d', format: 'rgba32float', usage: GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.COPY_DST });
    const texels = new Float32Array(8 * 4 * 4 * 4);
    for (let block = 0; block < 2; block++) for (let z = 0; z < 2; z++) for (let y = 0; y < 2; y++) for (let x = 0; x < 2; x++) {
        const index = ((z + 1) * 32 + (y + 1) * 8 + x + 1 + block * 4) * 4;
        texels.set([(block - 1) * 2 + x + .5, 2 * (y + .5), -(z + .5), 0], index);
    }
    device.queue.writeBuffer(buffer, 0, table);
    device.queue.writeTexture({ texture }, texels, { bytesPerRow: 128, rowsPerImage: 4 }, [8, 4, 4]);
    const positions = new Float32Array([-1.25,.75,.75, -.25,.75,.75, .25,.75,.75, .75,.75,.75, 30,0,0]);
    try {
        host.output = { sparse: { buffer }, velocity: { texture }, level: Array.from(level), layer: [2,2,2], layerAndLevel: 0,
            ...(boundaries ? { boundaries: savedOutput.boundaries } : {}), layers: [{ id:0, blockSizeWorld:[2,2,2], layerAndLevel:0 }] };
        host.stats.activeBlocks = 2;
        const actual = await host.sampleVelocity(positions);
        let maximumError = 0;
        for (let i = 0; i < positions.length; i++) {
            let expected = i >= 12 ? 0 : positions[i] * [1, 2, -1][i % 3];
            if (boundaries && (i === 3 || i === 6)) expected = Math.sign(positions[i]) * .5;
            maximumError = Math.max(maximumError, Math.abs(actual[i] - expected));
        }
        if (maximumError > 1e-6) throw new Error(`Sparse world velocity oracle error ${maximumError}: ${actual}`);
        return { checked: actual.length, maximumError, negativeCoordinates: true, crossBlockInterpolation: true, missingBlocksZero: true, boundarySafeDonors: boundaries };
    } finally {
        host.output = savedOutput; host.stats.activeBlocks = savedBlocks;
        buffer.destroy(); texture.destroy();
    }
}
