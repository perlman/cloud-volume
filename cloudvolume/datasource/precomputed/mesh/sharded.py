import numpy as np

from ..sharding import ShardingSpecification, ShardReader
from ....storage import SimpleStorage
from ....mesh import Mesh
from ....lib import red

class ShardedMultiLevelPrecomputedMeshSource:
    def __init__(self, meta, cache, config, readonly=False):
        self.meta = meta
        self.cache = cache
        self.config = config
        self.readonly = bool(readonly)

        spec = ShardingSpecification.from_dict(self.meta.info['sharding'])
        self.reader = ShardReader(meta, cache, spec)

        print(spec)

    @property
    def path(self):
        return self.meta.mesh_path
    
    def get(self, segids):
        list_return = True
        if type(segids) in (int, float):
            list_return = False
            segids = [ int(segids) ]

        results = []
        for segid in segids:
            # Read the manifest (with a tweak to sharding.py to get the offset)
            binary, shard_file_offset = self.reader.get_data(segid, self.meta.mesh_path, return_offset=True)
            manifest = MultiLevelPrecomputedMeshManifest(binary)

            # Read the data for all LODs
            fragment_sizes = [ np.sum(lod_fragment_sizes) for lod_fragment_sizes in manifest.fragment_offsets ]
            total_fragment_size = np.sum(fragment_sizes)

            # Kludge to hijack sharding.py to read the data
            shard_file_name = self.reader.get_filename(segid)
            full_path = self.reader.meta.join(self.reader.meta.cloudpath, self.path)
            with SimpleStorage(full_path) as stor:
                binary = stor.get_file(shard_file_name,
                                    start=shard_file_offset - total_fragment_size,
                                    end=shard_file_offset)

            meshes = []
            for lod in range(manifest.num_lods):
                lod_binary = binary[int(np.sum(fragment_sizes[0:lod])) :
                                    int(np.sum(fragment_sizes[0:lod+1]))
                                   ]
                lod_meshes = []
                for frag in range(manifest.fragment_offsets[lod].shape[0]):
                    frag_binary = lod_binary[
                                            int(np.sum(manifest.fragment_offsets[lod][0:frag])) :
                                            int(np.sum(manifest.fragment_offsets[lod][0:frag+1]))
                                            ] 
                    mesh = Mesh.from_draco(frag_binary)
                    lod_meshes.append(mesh)
                meshes.append(lod_meshes)

            results.append(meshes)

        if list_return:
            return results
        else:
            return results[0]

class MultiLevelPrecomputedMeshManifest:
    # Parse the multi-resolution mesh manifest file format:
    # https://github.com/google/neuroglancer/blob/master/src/neuroglancer/datasource/precomputed/meshes.md
    # https://github.com/google/neuroglancer/blob/master/src/neuroglancer/mesh/multiscale.ts

    def __init__(self, binary):
        self._binary = binary

        # num_loads is the 7th word
        num_lods = int.from_bytes(self._binary[6*4:7*4], byteorder='little', signed=False)

        header_dt = np.dtype([('chunk_shape', np.float32, (3,)),
                        ('grid_origin', np.float32, (3,)),
                        ('num_lods', np.uint32),
                        ('lod_scales', np.float32, (num_lods,)),
                        ('vertex_offsets', np.float32, (num_lods,3)),
                        ('num_fragments_per_lod', np.uint32, (num_lods,))
                        ])
        self._header = np.frombuffer(self._binary[0:header_dt.itemsize], dtype=header_dt)

        offset = header_dt.itemsize

        self._fragment_positions = []
        self._fragment_offsets = []
        for lod in range(num_lods):
            # Read fragment positions
            pos_size = 4 * 3 * self.num_fragments_per_lod[lod]
            self._fragment_positions.append(
                np.frombuffer(self._binary[offset:offset + pos_size], dtype=np.uint32)
            )
            offset += pos_size

            # Read fragment sizes
            off_size = 4*self.num_fragments_per_lod[lod]
            self._fragment_offsets.append(
                np.frombuffer(self._binary[offset:offset + off_size], dtype=np.uint32)
            )
            offset += off_size

        # Make sure we read the entire manifest
        assert(offset == len(binary))

    @property
    def chunk_shape(self):
        return self._header['chunk_shape'][0]

    @property
    def grid_origin(self):
        return self._header['grid_origin'][0]

    @property
    def num_lods(self):
        return self._header['num_lods'][0]

    @property
    def lod_scales(self):
        return self._header['lod_scales'][0]

    @property
    def vertex_offsets(self):
        return self._header['vertex_offsets'][0]

    @property
    def num_fragments_per_lod(self):
        return self._header['num_fragments_per_lod'][0]

    @property
    def fragment_positions(self):
        return self._fragment_positions

    @property
    def fragment_offsets(self):
        return self._fragment_offsets

    @property
    def length(self):
        return len(self._binary)
