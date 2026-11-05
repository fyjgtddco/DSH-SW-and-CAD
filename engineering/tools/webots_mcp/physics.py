"""SI rigid-body math. No COM, application launch, files, or global state.

Column vectors, p_target = R @ p_source + t. Inertia is a physical tensor
about the COM, expressed in the named frame (not principal moments).
"""
import math

IDENTITY = ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.))
MASS_FIELDS = ('CenterOfMassX', 'CenterOfMassY', 'CenterOfMassZ', 'Volume',
               'Area', 'Mass', 'MomXX', 'MomYY', 'MomZZ', 'MomXY', 'MomZX', 'MomYZ')


def _numbers(values, count):
    if not isinstance(values, (list, tuple)) or len(values) != count:
        raise ValueError(f'expected {count} numbers')
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in values):
        raise ValueError('finite numeric values required')
    return [float(x) for x in values]


def _matrix(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError('expected 3 by 3 matrix')
    return [_numbers(row, 3) for row in value]


def _det(a):
    return (a[0][0]*(a[1][1]*a[2][2]-a[1][2]*a[2][1])
            - a[0][1]*(a[1][0]*a[2][2]-a[1][2]*a[2][0])
            + a[0][2]*(a[1][0]*a[2][1]-a[1][1]*a[2][0]))


def _psd(a):
    # All principal minors, not just leading minors, characterize PSD.
    return (all(a[i][i] >= -1e-12 for i in range(3))
            and all(a[i][i]*a[j][j]-a[i][j]**2 >= -1e-12 for i in range(3) for j in range(i))
            and _det(a) >= -1e-12)


def validate_inertia(value):
    a = _matrix(value)
    scale = max(abs(x) for row in a for x in row)
    if scale == 0:
        raise ValueError('zero inertia unsupported')
    b = [[x / scale for x in row] for row in a]
    if any(abs(b[i][j]-b[j][i]) > 1e-12 for i in range(3) for j in range(3)):
        raise ValueError('inertia must be symmetric')
    half_trace = sum(b[i][i] for i in range(3)) / 2
    covariance = [[(half_trace if i == j else 0)-b[i][j] for j in range(3)] for i in range(3)]
    if not _psd(b) or not _psd(covariance):
        raise ValueError('inertia is not physically realizable')
    return a


def validate_rotation(value):
    r = _matrix(value)
    if (abs(_det(r)-1) > 1e-9 or
        any(abs(sum(r[k][i]*r[k][j] for k in range(3))-(i == j)) > 1e-9
            for i in range(3) for j in range(3))):
        raise ValueError('proper orthonormal rotation required; scale/reflection unsupported')
    return r


def parse_mass_array(method, raw):
    """Read documented slots verbatim; this does NOT infer cross-term signs."""
    if method in ('IModelDocExtension.GetMassProperties2', 'IBody2.GetMassProperties'):
        fields = MASS_FIELDS
    elif method == 'IModelDocExtension.GetMassProperties':
        fields = MASS_FIELDS + ('Accuracy',)
    else:
        raise ValueError('unverified mass method: ' + str(method))
    return dict(zip(fields, _numbers(raw, len(fields))))


def sw_inertia_tensor(raw):
    """IMassProperty.GetMomentOfInertia(0) products -> physical tensor.

    Off-diagonal sign follows ROS SW exporter Inertia.SetMomentMatrix.
    Do not apply to an already converted tensor or unidentified legacy array.
    """
    v = _numbers(raw, 9)
    return validate_inertia([[v[3*i+j] * (1 if i == j else -1)
                              for j in range(3)] for i in range(3)])


def mass_properties(mass, com, inertia, *, length_unit, mass_unit, frame):
    """One explicit input-unit conversion; downstream APIs accept SI only."""
    if length_unit not in ('m', 'mm') or mass_unit not in ('kg', 'g'):
        raise ValueError('unsupported or missing units')
    if not isinstance(frame, str) or not frame.strip():
        raise ValueError('frame required')
    m = _numbers([mass], 1)[0] * (1 if mass_unit == 'kg' else .001)
    length = 1 if length_unit == 'm' else .001
    if m <= 0: raise ValueError('positive mass required')
    factor = (1 if mass_unit == 'kg' else .001) * length**2
    c = [x*length for x in _numbers(com, 3)]
    tensor = [[x*factor for x in row] for row in _matrix(inertia)]
    return {'mass_kg': m, 'com_m': c, 'inertia_kg_m2': validate_inertia(tensor),
            'frame': frame, 'units': 'SI', 'inertia_about': 'center_of_mass'}


def _checked(body, frame):
    if body.get('units') != 'SI' or body.get('frame') != frame or body.get('inertia_about') != 'center_of_mass':
        raise ValueError('SI COM tensor and matching source frame required')
    return mass_properties(body['mass_kg'], body['com_m'], body['inertia_kg_m2'],
                           length_unit='m', mass_unit='kg', frame=frame)


def sw_transform(raw):
    """IComponent2.Transform2.ArrayData -> column-vector R,t in metres.

    SW stores row-vector axes; transpose its first nine entries exactly once.
    Component Transform2 is relative to root assembly, even for subassemblies.
    """
    v = _numbers(raw, 16)
    if v[12] != 1:
        raise ValueError('scaled component transform unsupported')
    return validate_rotation([[v[j*3+i] for j in range(3)] for i in range(3)]), v[9:12]


def transform_mass(body, rotation, translation_m, *, source_frame, target_frame):
    b = _checked(body, source_frame)
    r, t = validate_rotation(rotation), _numbers(translation_m, 3)
    c = [sum(r[i][j]*b['com_m'][j] for j in range(3))+t[i] for i in range(3)]
    a = b['inertia_kg_m2']
    tensor = [[sum(r[i][k]*a[k][l]*r[j][l] for k in range(3) for l in range(3))
               for j in range(3)] for i in range(3)]
    return mass_properties(b['mass_kg'], c, tensor, length_unit='m', mass_unit='kg', frame=target_frame)


def merge_rigid_bodies(components, *, frame):
    """Merge explicit fixed-group members (id, SI properties in one frame).

    A02 decides membership; this function never infers fixedness from contact.
    """
    members = list(components)
    ids = [item[0] for item in members]
    if not ids or any(not isinstance(x, str) or not x.strip() for x in ids) or len(set(ids)) != len(ids):
        raise ValueError('unique nonempty component ids required')
    bodies = [_checked(b, frame) for _, b in members]
    mass = math.fsum(b['mass_kg'] for b in bodies)
    com = [math.fsum(b['mass_kg']*b['com_m'][i] for b in bodies)/mass for i in range(3)]
    terms = []
    for b in bodies:
        d = [b['com_m'][i]-com[i] for i in range(3)]
        norm2 = sum(x*x for x in d)
        terms.append([[b['inertia_kg_m2'][i][j] + b['mass_kg']*((norm2 if i == j else 0)-d[i]*d[j])
                       for j in range(3)] for i in range(3)])
    tensor = [[math.fsum(a[i][j] for a in terms) for j in range(3)] for i in range(3)]
    result = mass_properties(mass, com, tensor, length_unit='m', mass_unit='kg', frame=frame)
    result['component_ids'] = ids
    return result
